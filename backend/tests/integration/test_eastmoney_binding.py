"""东财绑定与自选同步：真实 PostgreSQL，东财一侧用假的账号对象。"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.secret_box import SecretBox
from app.db.models.account import User
from app.db.models.market import Instrument
from app.db.session import create_db_engine
from app.providers.eastmoney_account import (
    EastmoneyLoginExpired,
    FavorGroup,
    QrPoll,
)
from app.providers.errors import ProviderError
from app.repositories.watchlists import WatchlistRepository
from app.services import eastmoney_binding as binding_module
from app.services.eastmoney_binding import EastmoneyBindingService
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

MOMENT = datetime(2026, 1, 1, tzinfo=UTC)
COOKIES = {"ct": "CT-SECRET", "ut": "UT-SECRET", "pi": "PI-SECRET", "uidal": "UID-SECRETnick"}
BOX = SecretBox("test-master-key")
# 699901 / 399902 不是真实代码，不会和真实入库数据冲突。
STOCKS = (("SSE", "699901", "1.699901"), ("SZSE", "399902", "0.399902"))


class FakeLogin:
    png = b"\x89PNG-fake"
    expired = False

    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FakeAccount:
    def __init__(self) -> None:
        self.groups: list[FavorGroup] = []
        self.error: Exception | None = None
        self.fetches = 0
        self.poll_state = "success"
        self.logins: list[FakeLogin] = []

    async def start_qr(self) -> FakeLogin:
        login = FakeLogin()
        self.logins.append(login)
        return login

    async def poll_qr(self, login: object) -> QrPoll:
        if self.poll_state == "success":
            return QrPoll(state="success", uid="1234567890123456", alias="老王", cookies=COOKIES)
        return QrPoll(state=self.poll_state)

    async def fetch_groups(self, cookies: Mapping[str, str]) -> list[FavorGroup]:
        self.fetches += 1
        assert cookies == COOKIES
        if self.error is not None:
            raise self.error
        return self.groups


class FakeInsights:
    def __init__(self) -> None:
        self.asked: list[list[str]] = []

    async def resolve_names(self, secids: Sequence[str]) -> dict[str, str]:
        self.asked.append(list(secids))
        return {secid: f"名称{secid}" for secid in secids if secid != "105.NONAME"}


@pytest.fixture
def engine() -> Iterator[Engine]:
    db = create_db_engine(get_settings().database_url)
    try:
        with db.connect() as connection:
            connection.execute(text("SELECT 1"))
        from alembic import command
        from alembic.config import Config

        command.upgrade(Config(str(Path(__file__).resolve().parents[2] / "alembic.ini")), "head")
    except SQLAlchemyError:
        pytest.skip("PostgreSQL 不可用")
    yield db
    users = "SELECT id FROM users WHERE username LIKE 'em-bind-%'"
    symbols = "SELECT id FROM instruments WHERE symbol IN ('699901','399902')"
    with db.begin() as connection:
        for statement in (
            "DELETE FROM watchlist_items WHERE group_id IN "
            f"(SELECT id FROM watchlist_groups WHERE user_id IN ({users}))",
            f"DELETE FROM watchlist_groups WHERE user_id IN ({users})",
            f"DELETE FROM eastmoney_bindings WHERE user_id IN ({users})",
            f"DELETE FROM market_bars WHERE instrument_id IN ({symbols})",
            "DELETE FROM instruments WHERE symbol IN ('699901','399902')",
            "DELETE FROM users WHERE username LIKE 'em-bind-%'",
        ):
            connection.execute(text(statement))
    db.dispose()


def _seed(engine: Engine) -> uuid.UUID:
    user_id = uuid.uuid4()
    with Session(engine) as session:
        session.add(
            User(
                id=user_id,
                username=f"em-bind-{user_id.hex[:8]}",
                email=f"{user_id.hex[:8]}@example.com",
                password_hash="x",
                role="researcher",
                is_active=True,
                created_at=MOMENT,
                updated_at=MOMENT,
            )
        )
        session.flush()
        for exchange, symbol, _ in STOCKS:
            session.add(
                Instrument(
                    id=uuid.uuid4(),
                    market="CN",
                    exchange=exchange,
                    symbol=symbol,
                    display_symbol=f"{symbol}.{'SH' if exchange == 'SSE' else 'SZ'}",
                    name_cn=f"样例{symbol}",
                    name_en="probe",
                    instrument_type="STOCK",
                    currency="CNY",
                    industry_id=None,
                    is_active=True,
                    list_date=None,
                    delist_date=None,
                    created_at=MOMENT,
                    updated_at=MOMENT,
                )
            )
        session.commit()
    return user_id


def _service(engine: Engine) -> tuple[EastmoneyBindingService, FakeAccount, FakeInsights]:
    account, insights = FakeAccount(), FakeInsights()
    service = EastmoneyBindingService(
        engine=engine,
        account=cast(Any, account),
        insights=cast(Any, insights),
        box=BOX,
    )
    return service, account, insights


def _bind(service: EastmoneyBindingService, user_id: uuid.UUID) -> binding_module.QrState:
    async def run() -> binding_module.QrState:
        started = await service.start_qr(user_id)
        return await service.poll_qr(user_id, started.login_id)

    return asyncio.run(run())


def _status(service: EastmoneyBindingService, user_id: uuid.UUID) -> tuple[str, str]:
    record = service.status(user_id)
    assert record is not None
    return record.status, record.em_alias


def _symbols(engine: Engine, user_id: uuid.UUID, name: str) -> dict[str, str]:
    """分组里每个标的 -> 来源（manual / eastmoney）。"""

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT COALESCE(i.symbol, wi.ext_secid), wi.source FROM watchlist_items wi "
                "JOIN watchlist_groups g ON g.id = wi.group_id "
                "LEFT JOIN instruments i ON i.id = wi.instrument_id "
                "WHERE g.user_id = :u AND g.name = :n"
            ),
            {"u": user_id, "n": name},
        ).all()
    return {str(symbol): str(source) for symbol, source in rows}


def test_scan_binds_encrypts_cookies_and_syncs_every_group(engine: Engine) -> None:
    user_id = _seed(engine)
    service, account, insights = _service(engine)
    account.groups = [
        FavorGroup(
            "11",
            "自选股",
            ("1.699901", "0.399902", "90.BK0473", "105.NONAME", "171.US10Y"),
        ),
        FavorGroup("22", "债券", ("1.511880",)),
    ]
    state = _bind(service, user_id)
    assert state.state == "success" and state.alias == "老王"
    assert state.sync is not None and state.sync.groups == 2 and state.sync.added == 6

    # 登录 cookie 只以密文落库：库里找不到任何明文值，但能用主密钥还原。
    with engine.connect() as connection:
        sealed = connection.execute(
            text("SELECT cookies_enc FROM eastmoney_bindings WHERE user_id = :u"), {"u": user_id}
        ).scalar_one()
    assert not any(value in sealed for value in COOKIES.values())
    assert json.loads(cast(str, BOX.open(sealed))) == COOKIES
    assert _status(service, user_id) == ("active", "老王")

    assert _symbols(engine, user_id, "自选股") == dict.fromkeys(
        ["699901", "399902", "90.BK0473", "105.NONAME", "171.US10Y"], "eastmoney"
    )
    assert set(_symbols(engine, user_id, "债券").values()) == {"eastmoney"}
    # 名称只给新出现的外部标的查一次；本系统已收录的 A 股不用查。
    asked = sorted(secid for batch in insights.asked for secid in batch)
    assert "1.699901" not in asked and "90.BK0473" in asked
    assert account.logins[0].closed


def test_resync_mirrors_removals_but_never_touches_manual_items(engine: Engine) -> None:
    user_id = _seed(engine)
    service, account, _ = _service(engine)
    account.groups = [FavorGroup("11", "自选股", ("1.699901", "90.BK0473"))]
    _bind(service, user_id)

    # 用户在本系统手动加了一只；东财里随后删掉了两只同步来的。
    group_id = next(g.id for g in WatchlistRepository(engine).list_groups(user_id))
    WatchlistRepository(engine).add_codes(user_id, group_id, [("SZSE", "399902")])
    account.groups = [FavorGroup("11", "自选股", ())]
    summary = asyncio.run(service.sync(user_id, force=True))
    # 东财给出空列表时保守处理：不移除任何已同步条目（可能是接口抖动）。
    assert summary.removed == 0

    account.groups = [FavorGroup("11", "自选股", ("1.699901",))]
    summary = asyncio.run(service.sync(user_id, force=True))
    assert summary.removed == 1
    names = _symbols(engine, user_id, "自选股")
    assert names == {"699901": "eastmoney", "399902": "manual"}


def test_failures_never_delete_synced_data(engine: Engine) -> None:
    user_id = _seed(engine)
    service, account, _ = _service(engine)
    account.groups = [FavorGroup("11", "自选股", ("1.699901", "90.BK0473"))]
    _bind(service, user_id)
    before = _symbols(engine, user_id, "自选股")

    account.error = ProviderError(code="UPSTREAM_DISCONNECTED", message="东财接口连接中断")
    with pytest.raises(AppError) as caught:
        asyncio.run(service.sync(user_id, force=True))
    assert caught.value.status_code == 502
    record = service.status(user_id)
    assert record is not None and record.status == "active"
    assert record.last_sync_message == "东财接口连接中断"

    # 某个分组读不到（secids=None）：整个分组保持原样，不当成空分组。
    account.error = None
    account.groups = [FavorGroup("11", "自选股", None, error="x")]
    summary = asyncio.run(service.sync(user_id, force=True))
    assert summary.skipped_groups == ("自选股",) and summary.groups == 0
    assert _symbols(engine, user_id, "自选股") == before


def test_expired_login_marks_binding_and_stops_calling_eastmoney(engine: Engine) -> None:
    user_id = _seed(engine)
    service, account, _ = _service(engine)
    account.groups = [FavorGroup("11", "自选股", ("1.699901",))]
    _bind(service, user_id)
    fetches = account.fetches

    account.error = EastmoneyLoginExpired(
        code="EASTMONEY_LOGIN_EXPIRED", message="东财登录已失效，请重新扫码"
    )
    with pytest.raises(AppError) as caught:
        asyncio.run(service.sync(user_id, force=True))
    assert caught.value.code == "EASTMONEY_LOGIN_EXPIRED" and caught.value.status_code == 409
    assert _status(service, user_id)[0] == "expired"
    assert _symbols(engine, user_id, "自选股") == {"699901": "eastmoney"}

    with pytest.raises(AppError):
        asyncio.run(service.sync(user_id, force=True))
    assert account.fetches == fetches + 1  # 失效后不再请求东财

    # 重新扫码即恢复，原有分组和条目继续使用。
    account.error = None
    state = _bind(service, user_id)
    assert state.state == "success" and _status(service, user_id)[0] == "active"


def test_sync_is_throttled_unless_forced(engine: Engine) -> None:
    user_id = _seed(engine)
    service, account, _ = _service(engine)
    account.groups = [FavorGroup("11", "自选股", ("1.699901",))]
    _bind(service, user_id)
    assert account.fetches == 1
    cached = asyncio.run(service.sync(user_id))
    assert cached.cached and account.fetches == 1 and cached.groups == 1
    fresh = asyncio.run(service.sync(user_id, force=True))
    assert not fresh.cached and account.fetches == 2


def test_same_name_group_is_adopted_not_duplicated(engine: Engine) -> None:
    user_id = _seed(engine)
    service, account, _ = _service(engine)
    repository = WatchlistRepository(engine)
    mine = repository.create_group(user_id, "自选股")
    assert mine is not None
    repository.add_codes(user_id, mine.id, [("SZSE", "399902")])

    account.groups = [FavorGroup("11", "自选股", ("1.699901",))]
    _bind(service, user_id)
    groups = repository.list_groups(user_id)
    assert [g.id for g in groups] == [mine.id]
    assert _symbols(engine, user_id, "自选股") == {"399902": "manual", "699901": "eastmoney"}

    # 东财分组改名后仍按分组 id 对上同一个分组。
    account.groups = [FavorGroup("11", "核心自选", ("1.699901",))]
    asyncio.run(service.sync(user_id, force=True))
    assert [g.id for g in repository.list_groups(user_id)] == [mine.id]


def test_qr_session_rules_and_unbind(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = _seed(engine)
    service, account, _ = _service(engine)

    async def run() -> None:
        first = await service.start_qr(user_id)
        # 二维码刚生成时不能连续刷新，防止打爆东财。
        with pytest.raises(AppError) as limited:
            await service.start_qr(user_id)
        assert limited.value.status_code == 429
        # 错误的 login_id（别人的或过期的）一律当作已失效，不泄露状态。
        assert (await service.poll_qr(user_id, "not-mine")).state == "expired"
        account.poll_state = "waiting"
        assert (await service.poll_qr(user_id, first.login_id)).state == "waiting"
        account.poll_state = "expired"
        assert (await service.poll_qr(user_id, first.login_id)).state == "expired"
        assert account.logins[0].closed
        assert service.status(user_id) is None

    asyncio.run(run())

    account.groups = [FavorGroup("11", "自选股", ("1.699901",))]
    account.poll_state = "success"
    monkeypatch.setattr(binding_module, "_MIN_QR_INTERVAL_SECONDS", 0)
    _bind(service, user_id)
    assert asyncio.run(service.unbind(user_id)) is True
    assert service.status(user_id) is None and asyncio.run(service.unbind(user_id)) is False
    # 解绑只删登录凭证，已同步的自选保留。
    assert _symbols(engine, user_id, "自选股") == {"699901": "eastmoney"}


def test_sync_all_continues_after_one_user_fails(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = _seed(engine), _seed_other(engine)
    service, account, _ = _service(engine)
    account.groups = [FavorGroup("11", "自选股", ("1.699901",))]
    monkeypatch.setattr(binding_module, "_MIN_QR_INTERVAL_SECONDS", 0)
    _bind(service, first)
    _bind(service, second)
    # 把两人的上次同步时间都拨到 10 分钟前，让后台循环真的去请求东财。
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE eastmoney_bindings SET last_sync_at = now() - interval '10 minutes' "
                "WHERE user_id IN (:a, :b)"
            ),
            {"a": first, "b": second},
        )

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(binding_module.asyncio, "sleep", no_sleep)
    calls = {"n": 0}
    original = account.fetch_groups

    async def flaky(cookies: Mapping[str, str]) -> list[FavorGroup]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ProviderError(code="UPSTREAM_DISCONNECTED", message="东财接口连接中断")
        return await original(cookies)

    monkeypatch.setattr(account, "fetch_groups", flaky)
    assert asyncio.run(service.sync_all()) == 1
    assert calls["n"] == 2


def _seed_other(engine: Engine) -> uuid.UUID:
    other = uuid.uuid4()
    with Session(engine) as session:
        session.add(
            User(
                id=other,
                username=f"em-bind-{other.hex[:8]}",
                email=f"{other.hex[:8]}@example.com",
                password_hash="x",
                role="researcher",
                is_active=True,
                created_at=MOMENT,
                updated_at=MOMENT,
            )
        )
        session.commit()
    return other
