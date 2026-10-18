import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Engine, or_, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.market import Instrument, MarketBar
from app.db.models.watchlist import WatchlistGroup, WatchlistItem
from app.utils.time import utc_now
from app.utils.watch_secid import (
    US_MARKET_BY_EXCHANGE,
    a_share_secid,
    display_symbol,
    make_secid,
    parse_secid,
)

# 东财给美股的市场编号；这些标的若本系统已收录，优先挂到本系统证券上（公司页可点）。
_US_MARKETS = (105, 106, 107)


@dataclass(frozen=True)
class WatchItem:
    """instrument_id 为空表示外部标的：只有东财 secid 与名称，行情现查。"""

    instrument_id: UUID | None
    display_symbol: str
    name_cn: str
    market: str = "CN"
    exchange: str = ""
    symbol: str = ""
    item_id: UUID | None = None
    # 东财 市场.代码，用来现查行情；本系统证券里推不出市场编号的为 None。
    secid: str | None = None


@dataclass(frozen=True)
class ImportOutcome:
    added: tuple[str, ...]
    existing: tuple[str, ...]
    missing: tuple[str, ...]
    # 与 missing 同一批，但保留交易所，供调用方改按东财代码保存。
    missing_keys: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ExternalTarget:
    """一个待保存的外部标的：东财 secid 和（可能没有的）名称。"""

    secid: str
    name: str | None = None


@dataclass(frozen=True)
class ExternalOutcome:
    added: tuple[str, ...]
    existing: tuple[str, ...]
    # 实际挂到本系统证券上的（如已收录的美股），不算外部标的。
    linked: tuple[str, ...]


@dataclass(frozen=True)
class RecentClose:
    trade_date: date
    close: Decimal


@dataclass(frozen=True)
class WatchGroup:
    id: UUID
    name: str
    created_at: datetime
    items: tuple[WatchItem, ...]


class WatchlistRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create_group(self, user_id: UUID, name: str) -> WatchGroup | None:
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("请填写分组名称")
        row = WatchlistGroup(
            id=uuid.uuid4(),
            user_id=user_id,
            name=cleaned[:40],
            created_at=utc_now(),
        )
        try:
            with Session(self.engine) as session, session.begin():
                session.add(row)
                session.flush()
                return WatchGroup(id=row.id, name=row.name, created_at=row.created_at, items=())
        except IntegrityError:
            return None

    def list_groups(self, user_id: UUID) -> list[WatchGroup]:
        with Session(self.engine) as session:
            groups = session.scalars(
                select(WatchlistGroup)
                .where(WatchlistGroup.user_id == user_id)
                .order_by(WatchlistGroup.created_at.desc())
            ).all()
            return [self._group(session, group) for group in groups]

    def add_item(self, user_id: UUID, group_id: UUID, instrument_id: UUID) -> bool | None:
        with Session(self.engine) as session, session.begin():
            group = session.get(WatchlistGroup, group_id)
            if group is None or group.user_id != user_id:
                return None
            if session.get(Instrument, instrument_id) is None:
                return False
            exists = session.scalars(
                select(WatchlistItem).where(
                    WatchlistItem.group_id == group_id,
                    WatchlistItem.instrument_id == instrument_id,
                )
            ).one_or_none()
            if exists is not None:
                return True
            session.add(
                WatchlistItem(
                    id=uuid.uuid4(),
                    group_id=group_id,
                    instrument_id=instrument_id,
                    created_at=utc_now(),
                )
            )
            return True

    def add_codes(
        self,
        user_id: UUID,
        group_id: UUID,
        codes: Sequence[tuple[str, str]],
        *,
        source: str = "manual",
    ) -> ImportOutcome | None:
        """按 (交易所, 代码) 批量加入；库里没有的证券原样列进 missing，不臆造。"""

        with Session(self.engine) as session, session.begin():
            group = session.get(WatchlistGroup, group_id)
            if group is None or group.user_id != user_id:
                return None
            found = (
                session.scalars(
                    select(Instrument).where(
                        Instrument.market == "CN",
                        tuple_(Instrument.exchange, Instrument.symbol).in_(list(codes)),
                    )
                ).all()
                if codes
                else []
            )
            by_key = {(row.exchange, row.symbol): row for row in found}
            present = set(
                session.scalars(
                    select(WatchlistItem.instrument_id).where(WatchlistItem.group_id == group_id)
                ).all()
            )
            added: list[str] = []
            existing: list[str] = []
            missing: list[str] = []
            missing_keys: list[tuple[str, str]] = []
            for key in dict.fromkeys(codes):
                row = by_key.get(key)
                if row is None:
                    missing.append(key[1])
                    missing_keys.append(key)
                    continue
                if row.id in present:
                    existing.append(row.display_symbol)
                    continue
                session.add(
                    WatchlistItem(
                        id=uuid.uuid4(),
                        group_id=group_id,
                        instrument_id=row.id,
                        source=source,
                        created_at=utc_now(),
                    )
                )
                present.add(row.id)
                added.append(row.display_symbol)
            return ImportOutcome(tuple(added), tuple(existing), tuple(missing), tuple(missing_keys))

    def add_external(
        self,
        user_id: UUID,
        group_id: UUID,
        targets: Sequence[ExternalTarget],
        *,
        source: str = "manual",
    ) -> ExternalOutcome | None:
        """保存本系统没有收录的标的（板块、指数、美股、基金、国债收益率……），按东财 secid 去重。

        美股若本系统已收录，则直接挂到本系统证券上，公司页等入口照常可用。
        """

        unique: dict[str, ExternalTarget] = {}
        for target in targets:
            parsed = parse_secid(target.secid)
            if parsed is not None:
                secid = make_secid(*parsed)
                unique.setdefault(secid, ExternalTarget(secid, target.name))
        with Session(self.engine) as session, session.begin():
            group = session.get(WatchlistGroup, group_id)
            if group is None or group.user_id != user_id:
                return None
            present_instruments = set(
                session.scalars(
                    select(WatchlistItem.instrument_id).where(
                        WatchlistItem.group_id == group_id,
                        WatchlistItem.instrument_id.is_not(None),
                    )
                ).all()
            )
            present_secids = set(
                session.scalars(
                    select(WatchlistItem.ext_secid).where(
                        WatchlistItem.group_id == group_id, WatchlistItem.ext_secid.is_not(None)
                    )
                ).all()
            )
            us_codes = [
                parsed[1]
                for parsed in map(parse_secid, unique)
                if parsed is not None and parsed[0] in _US_MARKETS
            ]
            us_rows = (
                session.scalars(
                    select(Instrument).where(
                        Instrument.market == "US", Instrument.symbol.in_(us_codes)
                    )
                ).all()
                if us_codes
                else []
            )
            added: list[str] = []
            existing: list[str] = []
            linked: list[str] = []
            for secid, target in unique.items():
                parsed = parse_secid(secid)
                if parsed is None:
                    continue
                market, code = parsed
                row = _pick_us(us_rows, market, code) if market in _US_MARKETS else None
                if row is not None:
                    if row.id in present_instruments:
                        existing.append(row.display_symbol)
                        continue
                    session.add(
                        WatchlistItem(
                            id=uuid.uuid4(),
                            group_id=group_id,
                            instrument_id=row.id,
                            source=source,
                            created_at=utc_now(),
                        )
                    )
                    present_instruments.add(row.id)
                    added.append(row.display_symbol)
                    linked.append(row.display_symbol)
                    continue
                label = display_symbol(secid)
                if secid in present_secids:
                    existing.append(label)
                    continue
                session.add(
                    WatchlistItem(
                        id=uuid.uuid4(),
                        group_id=group_id,
                        instrument_id=None,
                        ext_secid=secid,
                        ext_name=(target.name or "")[:128] or None,
                        source=source,
                        created_at=utc_now(),
                    )
                )
                present_secids.add(secid)
                added.append(label)
            return ExternalOutcome(tuple(added), tuple(existing), tuple(linked))

    def fill_external_names(self, user_id: UUID, group_id: UUID, names: Mapping[UUID, str]) -> int:
        """给导入时没取到名称的外部标的回填名称；已有名称的不覆盖。返回回填的行数。"""

        if not names:
            return 0
        with Session(self.engine) as session, session.begin():
            group = session.get(WatchlistGroup, group_id)
            if group is None or group.user_id != user_id:
                return 0
            rows = session.scalars(
                select(WatchlistItem).where(
                    WatchlistItem.group_id == group_id,
                    WatchlistItem.id.in_(list(names)),
                    WatchlistItem.ext_secid.is_not(None),
                    WatchlistItem.ext_name.is_(None),
                )
            ).all()
            for row in rows:
                row.ext_name = names[row.id][:128]
            return len(rows)

    def ensure_eastmoney_group(self, user_id: UUID, em_gid: str, name: str) -> tuple[UUID, bool]:
        """找到与东财分组对应的本系统分组，没有就建一个。返回 (分组 id, 是否新建)。

        先按东财分组 id 对；其次认领同名且还没绑定东财的分组（比如之前手动建过同名分组）；
        同名但已绑定别的东财分组时，名称后面加东财分组 id 末 4 位区分。
        """

        cleaned = name.strip()[:40] or em_gid[:40]
        with Session(self.engine) as session, session.begin():
            row = session.scalars(
                select(WatchlistGroup).where(
                    WatchlistGroup.user_id == user_id, WatchlistGroup.em_gid == em_gid
                )
            ).one_or_none()
            if row is not None:
                return row.id, False
            same = session.scalars(
                select(WatchlistGroup).where(
                    WatchlistGroup.user_id == user_id, WatchlistGroup.name == cleaned
                )
            ).one_or_none()
            if same is not None and same.em_gid is None:
                same.em_gid = em_gid
                return same.id, False
            if same is not None:
                cleaned = f"{cleaned[:35]}·{em_gid[-4:]}"
            created = WatchlistGroup(
                id=uuid.uuid4(),
                user_id=user_id,
                name=cleaned,
                em_gid=em_gid,
                created_at=utc_now(),
            )
            session.add(created)
            session.flush()
            return created.id, True

    def group_ext_secids(self, user_id: UUID, group_id: UUID) -> set[str]:
        """分组里已按东财 secid 保存的外部标的，同步时用来判断哪些名称还没取过。"""

        with Session(self.engine) as session:
            group = session.get(WatchlistGroup, group_id)
            if group is None or group.user_id != user_id:
                return set()
            return {
                secid
                for secid in session.scalars(
                    select(WatchlistItem.ext_secid).where(
                        WatchlistItem.group_id == group_id, WatchlistItem.ext_secid.is_not(None)
                    )
                ).all()
                if secid
            }

    def remove_stale_synced(self, user_id: UUID, group_id: UUID, keep: set[str]) -> int:
        """移除「同步来的、但东财分组里已经没有」的条目；用户手动加的永远不动。"""

        keep_us = {
            parsed[1]
            for parsed in map(parse_secid, keep)
            if parsed is not None and parsed[0] in _US_MARKETS
        }
        with Session(self.engine) as session, session.begin():
            group = session.get(WatchlistGroup, group_id)
            if group is None or group.user_id != user_id:
                return 0
            rows = session.scalars(
                select(WatchlistItem).where(
                    WatchlistItem.group_id == group_id, WatchlistItem.source == "eastmoney"
                )
            ).all()
            ids = [row.instrument_id for row in rows if row.instrument_id is not None]
            instruments = (
                {
                    row.id: row
                    for row in session.scalars(
                        select(Instrument).where(Instrument.id.in_(ids))
                    ).all()
                }
                if ids
                else {}
            )
            removed = 0
            for row in rows:
                if row.instrument_id is None:
                    stale = (row.ext_secid or "") not in keep
                else:
                    instrument = instruments.get(row.instrument_id)
                    if instrument is None:
                        continue
                    stale = _instrument_secid(instrument) not in keep and not (
                        instrument.market == "US" and instrument.symbol in keep_us
                    )
                if stale:
                    session.delete(row)
                    removed += 1
            return removed

    def get_group(self, user_id: UUID, group_id: UUID) -> WatchGroup | None:
        with Session(self.engine) as session:
            group = session.get(WatchlistGroup, group_id)
            if group is None or group.user_id != user_id:
                return None
            return self._group(session, group)

    def recent_closes(
        self, instrument_ids: Sequence[UUID], since: date
    ) -> dict[UUID, list[RecentClose]]:
        """已入库的不复权日线收盘价（任意来源），供实时行情不可用时计算涨跌幅。"""

        if not instrument_ids:
            return {}
        with Session(self.engine) as session:
            rows = session.execute(
                select(MarketBar.instrument_id, MarketBar.trade_date, MarketBar.close)
                .where(
                    MarketBar.instrument_id.in_(list(instrument_ids)),
                    MarketBar.timeframe == "1d",
                    MarketBar.adjustment == "none",
                    MarketBar.trade_date >= since,
                )
                .order_by(MarketBar.instrument_id, MarketBar.trade_date)
            ).all()
        result: dict[UUID, dict[date, Decimal]] = {}
        for instrument_id, trade_date, close in rows:
            result.setdefault(instrument_id, {})[trade_date] = close
        return {
            key: [RecentClose(day, close) for day, close in sorted(days.items())]
            for key, days in result.items()
        }

    def remove_item(self, user_id: UUID, group_id: UUID, ref: UUID) -> bool | None:
        """ref 可以是条目 id（外部标的只有这个）或本系统证券 id，两种 UUID 不会重合。"""

        with Session(self.engine) as session, session.begin():
            group = session.get(WatchlistGroup, group_id)
            if group is None or group.user_id != user_id:
                return None
            item = session.scalars(
                select(WatchlistItem).where(
                    WatchlistItem.group_id == group_id,
                    or_(WatchlistItem.id == ref, WatchlistItem.instrument_id == ref),
                )
            ).first()
            if item is None:
                return False
            session.delete(item)
            return True

    def _group(self, session: Session, group: WatchlistGroup) -> WatchGroup:
        rows = session.scalars(
            select(WatchlistItem)
            .where(WatchlistItem.group_id == group.id)
            .order_by(WatchlistItem.created_at.desc())
        ).all()
        ids = [row.instrument_id for row in rows if row.instrument_id is not None]
        instruments = (
            {
                row.id: row
                for row in session.scalars(select(Instrument).where(Instrument.id.in_(ids))).all()
            }
            if ids
            else {}
        )
        items: list[WatchItem] = []
        for row in rows:
            if row.instrument_id is None:
                items.append(_external_item(row))
                continue
            instrument = instruments.get(row.instrument_id)
            if instrument is None:
                continue
            items.append(
                WatchItem(
                    instrument_id=instrument.id,
                    display_symbol=instrument.display_symbol,
                    name_cn=instrument.name_cn,
                    market=instrument.market,
                    exchange=instrument.exchange,
                    symbol=instrument.symbol,
                    item_id=row.id,
                    secid=_instrument_secid(instrument),
                )
            )
        return WatchGroup(
            id=group.id, name=group.name, created_at=group.created_at, items=tuple(items)
        )


def _instrument_secid(instrument: Instrument) -> str | None:
    if instrument.market == "CN" and len(instrument.symbol) == 6:
        return a_share_secid(instrument.exchange, instrument.symbol)
    if instrument.market == "US":
        market = US_MARKET_BY_EXCHANGE.get(instrument.exchange)
        return None if market is None else make_secid(market, instrument.symbol)
    return None


def _external_item(row: WatchlistItem) -> WatchItem:
    secid = row.ext_secid or ""
    parsed = parse_secid(secid)
    market, code = parsed if parsed is not None else (-1, secid)
    return WatchItem(
        instrument_id=None,
        display_symbol=display_symbol(secid),
        name_cn=row.ext_name or code,
        market="EXT",
        exchange=str(market),
        symbol=code,
        item_id=row.id,
        secid=secid or None,
    )


def _pick_us(rows: Sequence[Instrument], market: int, code: str) -> Instrument | None:
    """同一代码可能同时在多个交易所有记录，优先取东财市场编号对得上的那条。"""

    same = [row for row in rows if row.symbol == code]
    exact = [row for row in same if US_MARKET_BY_EXCHANGE.get(row.exchange) == market]
    chosen = exact or same
    return chosen[0] if chosen else None
