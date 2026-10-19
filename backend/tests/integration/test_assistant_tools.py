"""助手数据工具（规格 §7，第 1b 期）：真实数据库上的证券搜索、日线、自选、中国序列、历史时点。

测试数据都用独有的代码和远古日期，结束时清掉，不污染开发库里的真实数据。
"""

from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.catalog.reference_data import InstrumentSeed
from app.core.config import get_settings
from app.core.security import PasswordService
from app.db.session import create_db_engine
from app.providers.quality import CheckedBar
from app.repositories.accounts import AccountRepository
from app.repositories.instruments import InstrumentRepository
from app.repositories.macro_china import MacroChinaRepository
from app.repositories.market_bars import MarketBarRepository
from app.repositories.watchlists import WatchlistRepository
from app.services.assistant.tool_cache import ToolCache
from app.services.assistant.tools import ToolOutcome, ToolRegistry, default_registry
from app.services.assistant.tools.base import ToolContext
from app.services.china_macro import CsvObservation
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

SYMBOL = "999972"
PASSWORD = "correct-password"
NOW = datetime(2026, 10, 19, 4, 0, tzinfo=UTC)  # 上海 10-19 12:00
CLOSES = {
    date(2026, 10, 12): "100.5",
    date(2026, 10, 13): "101",
    date(2026, 10, 14): "102.25",
    date(2026, 10, 15): "103",
    date(2026, 10, 16): "110",
}


def _ready() -> bool:
    engine = create_db_engine(get_settings().database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        root = Path(__file__).resolve().parents[2]
        command.upgrade(Config(str(root / "alembic.ini")), "head")
        return True
    except SQLAlchemyError:
        return False
    finally:
        engine.dispose()


@pytest.fixture
def engine() -> Iterator[Engine]:
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    created = create_db_engine(get_settings().database_url)
    _cleanup(created)
    yield created
    _cleanup(created)
    created.dispose()


def _cleanup(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM watchlist_groups WHERE user_id IN "
                "(SELECT id FROM users WHERE username LIKE 't30-%')"
            )
        )
        connection.execute(
            text(
                "DELETE FROM market_bars WHERE instrument_id IN "
                "(SELECT id FROM instruments WHERE symbol = :s)"
            ),
            {"s": SYMBOL},
        )
        connection.execute(text("DELETE FROM instruments WHERE symbol = :s"), {"s": SYMBOL})
        connection.execute(
            text(
                "DELETE FROM macro_observations WHERE observation_date < '2000-01-01' AND "
                "series_id IN (SELECT id FROM macro_series WHERE series_code = 'CPI_CN')"
            )
        )
        connection.execute(text("DELETE FROM users WHERE username LIKE 't30-%'"))


@pytest.fixture
def make_user(engine: Engine) -> Callable[[], UUID]:
    def create() -> UUID:
        username = f"t30-{uuid4().hex[:8]}"
        return (
            AccountRepository(engine)
            .create_user(
                username=username,
                email=f"{username}@example.com",
                password_hash=PasswordService("test").hash(PASSWORD),
                role="researcher",
            )
            .id
        )

    return create


def _seed_instrument(engine: Engine) -> UUID:
    InstrumentRepository(engine).upsert_reference(
        (InstrumentSeed("CN", "SZSE", SYMBOL, "工具测试公司", "Pytest Tools Co", "STOCK", "CNY"),)
    )
    with engine.connect() as connection:
        found = connection.execute(
            text("SELECT id FROM instruments WHERE symbol = :s"), {"s": SYMBOL}
        ).scalar_one()
    bars = MarketBarRepository(engine)
    bars.upsert(
        [
            CheckedBar(
                trade_date=day,
                open=Decimal(close),
                high=Decimal(close),
                low=Decimal(close),
                close=Decimal(close),
                volume=Decimal(1000),
                amount=None,
                adjustment="none",
                currency="CNY",
                unit="CNY",
                source_record_key=f"t30-{SYMBOL}-{day.isoformat()}",
                quality_flag="ok",
            )
            for day, close in CLOSES.items()
        ],
        instrument_id=found,
        provider_id=bars.provider_id_for_market("CN"),
    )
    return UUID(str(found))


def _ctx(engine: Engine, user_id: UUID | None = None, now: datetime = NOW) -> ToolContext:
    return ToolContext(engine=engine, user_id=user_id or uuid4(), now=now)


def _search(registry: ToolRegistry, ctx: ToolContext) -> ToolOutcome:
    return registry.execute("instrument_search", f'{{"query": "{SYMBOL}"}}', ctx)


def test_price_bars_only_accepts_ids_returned_by_this_turns_search(engine: Engine) -> None:
    instrument_id = _seed_instrument(engine)
    registry = default_registry()
    ctx = _ctx(engine)
    args = f'{{"instrument_id": "{instrument_id}"}}'

    blind = registry.execute("price_bars", args, ctx)
    assert blind.result.status == "unavailable"
    assert "instrument_search" in (blind.result.reason or "")

    found = _search(registry, ctx)
    assert found.result.status == "ok"
    assert found.result.rows[0]["symbol"] == f"{SYMBOL}.SZ"
    assert instrument_id in ctx.searched

    bars = registry.execute("price_bars", args, ctx).result
    assert bars.status == "ok" and bars.primary == "close"
    assert [row["close"] for row in bars.rows] == ["100.5", "101", "102.25", "103", "110"]
    assert bars.rows[-1]["trade_date"] == "2026-10-16"
    assert bars.as_of == date(2026, 10, 19) and "工具测试公司" in bars.title

    stranger = registry.execute("price_bars", args, _ctx(engine))  # 另一个回合没搜过
    assert stranger.result.status == "unavailable"


def test_price_bars_respects_point_in_time_and_flags_stale_data(engine: Engine) -> None:
    instrument_id = _seed_instrument(engine)
    registry = default_registry()
    ctx = _ctx(engine)
    _search(registry, ctx)
    past = registry.execute(
        "price_bars", f'{{"instrument_id": "{instrument_id}", "as_of": "2026-10-14"}}', ctx
    ).result
    assert [row["trade_date"] for row in past.rows][-1] == "2026-10-14"
    assert past.status == "ok"

    later = _ctx(engine, now=datetime(2026, 12, 1, 4, 0, tzinfo=UTC))
    _search(registry, later)
    stale = registry.execute("price_bars", f'{{"instrument_id": "{instrument_id}"}}', later).result
    assert stale.status == "stale" and "2026-10-16" in (stale.reason or "")


def test_price_bars_rejects_bad_ids_and_oversized_windows(engine: Engine) -> None:
    instrument_id = _seed_instrument(engine)
    registry = default_registry()
    ctx = _ctx(engine)
    _search(registry, ctx)
    wide = registry.execute(
        "price_bars",
        f'{{"instrument_id": "{instrument_id}", "start": "2020-01-01", "end": "2026-01-01"}}',
        ctx,
    ).result
    assert wide.status == "unavailable" and "不能超过 3 年" in (wide.reason or "")
    bad = registry.execute("price_bars", '{"instrument_id": "not-a-uuid"}', ctx).result
    assert bad.status == "unavailable"
    empty = registry.execute(
        "price_bars",
        f'{{"instrument_id": "{instrument_id}", "start": "2025-01-01", "end": "2025-02-01"}}',
        ctx,
    ).result
    assert empty.status == "unavailable" and "没有已入库的日线" in (empty.reason or "")


def test_instrument_search_without_match_says_so(engine: Engine) -> None:
    registry = default_registry()
    outcome = registry.execute("instrument_search", '{"query": "999979"}', _ctx(engine))
    assert outcome.result.status == "unavailable"
    assert "没有收录" in (outcome.result.reason or "")


def test_cached_search_still_registers_ids_for_the_new_turn(engine: Engine) -> None:
    instrument_id = _seed_instrument(engine)
    registry = default_registry(ToolCache())
    first_ctx, second_ctx = _ctx(engine), _ctx(engine)
    first = _search(registry, first_ctx)
    second = _search(registry, second_ctx)
    assert (first.cached, second.cached) == (False, True)
    assert instrument_id in second_ctx.searched
    ok = registry.execute("price_bars", f'{{"instrument_id": "{instrument_id}"}}', second_ctx)
    assert ok.result.status == "ok"


def test_calc_reproduces_the_period_return_from_bars_evidence(engine: Engine) -> None:
    instrument_id = _seed_instrument(engine)
    registry = default_registry()
    ctx = _ctx(engine)
    _search(registry, ctx)
    ctx.book.add(
        registry.execute("price_bars", f'{{"instrument_id": "{instrument_id}"}}', ctx).result
    )
    evidence = ctx.book.get("E1")
    assert evidence is not None
    outcome = registry.execute(
        "calc",
        '{"op": "pct_change", "inputs": [{"evidence": "E1", "column": "close", "row": -1},'
        ' {"evidence": "E1", "column": "close", "row": 1}]}',
        ctx,
    )
    assert outcome.result.rows == ({"value": "9.4527"},)  # (110-100.5)/100.5*100


def test_watchlist_reads_only_the_current_users_groups(
    engine: Engine, make_user: Callable[[], UUID]
) -> None:
    instrument_id = _seed_instrument(engine)
    owner, other = make_user(), make_user()
    registry = default_registry(ToolCache())
    empty = registry.execute("watchlist", "{}", _ctx(engine, owner)).result
    assert empty.status == "unavailable" and "还没有标的" in (empty.reason or "")

    repository = WatchlistRepository(engine)
    group = repository.create_group(owner, "测试分组")
    assert group is not None
    assert repository.add_item(owner, group.id, instrument_id)

    mine = registry.execute("watchlist", "{}", _ctx(engine, owner))
    assert mine.result.status == "ok" and mine.cached is False
    assert mine.result.rows[0]["symbol"] == f"{SYMBOL}.SZ"
    assert (
        mine.result.rows[0]["close"] == "110" and mine.result.rows[0]["trade_date"] == "2026-10-16"
    )
    theirs = registry.execute("watchlist", "{}", _ctx(engine, other))
    assert theirs.result.status == "unavailable"  # 不会读到别人的，也不会命中缓存
    again = registry.execute("watchlist", "{}", _ctx(engine, owner))
    assert again.cached is False  # watchlist 不缓存


def test_custom_indicator_refuses_historical_points_in_time(engine: Engine) -> None:
    registry = default_registry()
    past = registry.execute(
        "custom_indicator", '{"indicator": "valuation", "as_of": "2026-01-02"}', _ctx(engine)
    ).result
    assert past.status == "unavailable"
    assert "只能回答当前" in (past.reason or "")
    pcr_past = registry.execute(
        "custom_indicator",
        '{"indicator": "pcr", "group": "csi300", "as_of": "2026-01-02"}',
        _ctx(engine),
    ).result
    assert pcr_past.status == "unavailable"


def test_custom_indicator_validates_group_and_returns_consistent_results(engine: Engine) -> None:
    registry = default_registry()
    missing = registry.execute("custom_indicator", '{"indicator": "pcr"}', _ctx(engine)).result
    assert missing.status == "unavailable" and "需要 group" in (missing.reason or "")
    unknown = registry.execute(
        "custom_indicator", '{"indicator": "pcr", "group": "nope"}', _ctx(engine)
    ).result
    assert "没有这个分组" in (unknown.reason or "")
    for args in ('{"indicator": "pcr", "group": "csi300"}', '{"indicator": "valuation"}'):
        result = registry.execute("custom_indicator", args, _ctx(engine)).result
        # 库里有没有数据取决于环境；无论哪种，结果都必须自洽，不抛异常
        assert bool(result.rows) == (result.status != "unavailable")
        if result.status == "unavailable":
            assert result.reason


def test_china_series_is_read_through_macro_repository(engine: Engine) -> None:
    """规格 R7：中国序列与美国序列同表，`macro_series` 统一经 `MacroRepository` 读出。"""

    MacroChinaRepository(engine).import_observations(
        [
            CsvObservation(
                "CPI_CN", date(1998, 4, 1), Decimal("0.8"), "Percent", "t30", date(1998, 5, 15)
            ),
            CsvObservation(
                "CPI_CN", date(1998, 5, 1), Decimal("0.9"), "Percent", "t30", date(1998, 6, 15)
            ),
        ]
    )
    registry = default_registry()
    base = '{"code": "CPI_CN", "start": "1998-01-01", "end": "1998-12-31"'
    both = registry.execute("macro_series", base + ', "as_of": "1998-06-20"}', _ctx(engine)).result
    assert both.status == "ok"
    assert [row["value"] for row in both.rows] == ["0.8", "0.9"]
    assert both.source.startswith("国家统计局")
    first_only = registry.execute(
        "macro_series", base + ', "as_of": "1998-06-01"}', _ctx(engine)
    ).result
    assert [row["value"] for row in first_only.rows] == ["0.8"]  # 第二条 6-15 才公布
    on_release_day = registry.execute(
        "macro_series", base + ', "as_of": "1998-06-15"}', _ctx(engine)
    ).result
    assert [row["value"] for row in on_release_day.rows] == ["0.8"]  # 发布日当天不算已公布
