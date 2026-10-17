import uuid
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.catalog.macro_series import MacroProvider, MacroSeriesSeed, fred_series_catalog
from app.db.models.macro import MacroObservation, MacroSeries
from app.db.models.provider import DataProvider
from app.providers.eastmoney_us_macro import LatestValue, indicator_url
from app.providers.fred import FredVintage
from app.providers.latest_revisions import plan_latest_revisions
from app.providers.vintage import VintagePoint, pit_date, published_at_from_vintage

FRED_PROVIDER_ID = uuid.uuid5(uuid.NAMESPACE_URL, "https://finscope.local/providers/fred")
EASTMONEY_US_PROVIDER_ID = uuid.uuid5(
    uuid.NAMESPACE_URL, "https://finscope.local/providers/eastmoney-us-macro"
)
_PROVIDERS: dict[MacroProvider, tuple[uuid.UUID, str, str, dict[str, object]]] = {
    "fred": (
        FRED_PROVIDER_ID,
        "fred",
        "FRED",
        {"transport": "https://api.stlouisfed.org", "credential": "environment"},
    ),
    "eastmoney": (
        EASTMONEY_US_PROVIDER_ID,
        "eastmoney_us_macro",
        "东方财富数据中心（美国经济数据）",
        {"transport": "https://datacenter-web.eastmoney.com", "credential": "none"},
    ),
}


class MacroRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def ensure_catalog(self) -> int:
        seeds = fred_series_catalog()
        with Session(self.engine) as session:
            providers = {
                code: _provider(session, code) for code in {seed.provider for seed in seeds}
            }
            for seed in seeds:
                _upsert_series(session, provider_id=providers[seed.provider].id, seed=seed)
            session.commit()
        return len(seeds)

    def upsert_vintages(
        self,
        series_code: str,
        vintages: tuple[FredVintage, ...] | list[FredVintage],
        *,
        fetched_at: datetime,
    ) -> int:
        self.ensure_catalog()
        written = 0
        with Session(self.engine) as session:
            series = session.scalars(
                select(MacroSeries).where(MacroSeries.series_code == series_code)
            ).one()
            touched: set[date] = set()
            temporary = -1
            for vintage in vintages:
                touched.add(vintage.observation_date)
                existing = session.scalars(
                    select(MacroObservation).where(
                        MacroObservation.series_id == series.id,
                        MacroObservation.observation_date == vintage.observation_date,
                        MacroObservation.realtime_start == vintage.realtime_start,
                    )
                ).one_or_none()
                if existing is None:
                    session.add(
                        _observation(
                            series,
                            vintage,
                            fetched_at=fetched_at,
                            revision_no=temporary,
                        )
                    )
                    temporary -= 1
                else:
                    existing.value = vintage.value
                    existing.unit = series.base_unit
                    existing.realtime_end = vintage.realtime_end
                    existing.published_at = published_at_from_vintage(vintage.realtime_start)
                    existing.fetched_at = fetched_at
                    existing.source_record_key = _record_key(series.series_code, vintage)
                written += 1
            session.flush()
            for observed in touched:
                _renumber(session, series_id=series.id, observation_date=observed)
            session.commit()
        return written

    def upsert_latest_values(
        self,
        series_code: str,
        points: Sequence[LatestValue],
        *,
        fetched_at: datetime,
    ) -> int:
        """只有最新值的来源：与已入库版本比对后，只写新增或变化的版本。"""

        existing = self.list_vintages(series_code, start=None, end=None) or []
        planned = plan_latest_revisions(existing, points, fetched_on=pit_date(fetched_at))
        if not planned:
            return 0
        return self.upsert_vintages(series_code, planned, fetched_at=fetched_at)

    def list_series(self) -> list[MacroSeriesSeed]:
        self.ensure_catalog()
        with Session(self.engine) as session:
            rows = session.scalars(
                select(MacroSeries)
                .where(MacroSeries.enabled.is_(True))
                .order_by(MacroSeries.series_code)
            ).all()
            return [_seed_from_row(row) for row in rows]

    def list_vintages(
        self,
        series_code: str,
        *,
        start: date | None,
        end: date | None,
    ) -> list[VintagePoint] | None:
        self.ensure_catalog()
        with Session(self.engine) as session:
            series = session.scalars(
                select(MacroSeries).where(MacroSeries.series_code == series_code)
            ).one_or_none()
            if series is None:
                return None
            statement = select(MacroObservation).where(MacroObservation.series_id == series.id)
            if start is not None:
                statement = statement.where(MacroObservation.observation_date >= start)
            if end is not None:
                statement = statement.where(MacroObservation.observation_date <= end)
            rows = session.scalars(statement.order_by(MacroObservation.observation_date)).all()
            points: list[VintagePoint] = []
            for row in rows:
                point = _point(row)
                if point is not None:
                    points.append(point)
            return points

    def list_vintages_many(self, series_codes: Sequence[str]) -> dict[str, list[VintagePoint]]:
        """批量读取：只同步一次目录、只查一次库。"""

        self.ensure_catalog()
        found: dict[str, list[VintagePoint]] = {code: [] for code in series_codes}
        if not found:
            return found
        with Session(self.engine) as session:
            rows = session.execute(
                select(MacroSeries.series_code, MacroObservation)
                .join(MacroSeries, MacroObservation.series_id == MacroSeries.id)
                .where(MacroSeries.series_code.in_(list(found)))
                .order_by(MacroObservation.observation_date)
            ).all()
            for series_code, row in rows:
                point = _point(row)
                if point is not None:
                    found[series_code].append(point)
        return found


def _point(row: MacroObservation) -> VintagePoint | None:
    if row.realtime_start is None or row.realtime_end is None:
        return None
    return VintagePoint(
        observation_date=row.observation_date,
        value=row.value,
        unit=row.unit,
        realtime_start=row.realtime_start,
        realtime_end=row.realtime_end,
        revision_no=row.revision_no,
    )


def _provider(session: Session, code: MacroProvider) -> DataProvider:
    provider_id, provider_code, name, config = _PROVIDERS[code]
    found = session.scalars(
        select(DataProvider).where(DataProvider.code == provider_code)
    ).one_or_none()
    if found is not None:
        return found
    provider = DataProvider(
        id=provider_id,
        code=provider_code,
        name=name,
        enabled=True,
        config_public=config,
    )
    session.add(provider)
    session.flush()
    return provider


def _upsert_series(session: Session, *, provider_id: uuid.UUID, seed: MacroSeriesSeed) -> None:
    existing = session.scalars(
        select(MacroSeries).where(MacroSeries.series_code == seed.series_code)
    ).one_or_none()
    metadata = _metadata(seed)
    if existing is None:
        session.add(
            MacroSeries(
                id=uuid.uuid5(
                    uuid.NAMESPACE_URL, f"https://finscope.local/series/{seed.series_code}"
                ),
                provider_id=provider_id,
                series_code=seed.series_code,
                name_cn=seed.name_cn,
                name_en=seed.name_en,
                region="US",
                category=seed.category,
                frequency=seed.frequency,
                base_unit=seed.base_unit,
                seasonal_adjustment=seed.seasonal_adjustment,
                transform_rule="level",
                enabled=True,
                metadata_json=metadata,
            )
        )
        return
    existing.provider_id = provider_id
    existing.name_cn = seed.name_cn
    existing.name_en = seed.name_en
    existing.category = seed.category
    existing.frequency = seed.frequency
    existing.base_unit = seed.base_unit
    existing.seasonal_adjustment = seed.seasonal_adjustment
    existing.transform_rule = "level"
    existing.enabled = True
    existing.metadata_json = metadata


def _metadata(seed: MacroSeriesSeed) -> dict[str, object]:
    source_url = (
        indicator_url(seed.source_key)
        if seed.provider == "eastmoney"
        else f"https://fred.stlouisfed.org/series/{seed.series_code}"
    )
    return {
        "official_title": seed.name_en,
        "unit_label": seed.base_unit,
        "seasonal_label": seed.seasonal_adjustment,
        "time_precision": "date",
        "source_url": source_url,
        "source_agency": seed.source_agency,
        "provider": seed.provider,
        "source_key": seed.source_key,
        "vintage_calendar": "America/New_York",
        "note": "频率和单位不同的序列不能直接相加",
    }


def _observation(
    series: MacroSeries,
    vintage: FredVintage,
    *,
    fetched_at: datetime,
    revision_no: int,
) -> MacroObservation:
    return MacroObservation(
        id=uuid.uuid4(),
        series_id=series.id,
        observation_date=vintage.observation_date,
        value=vintage.value,
        unit=series.base_unit,
        realtime_start=vintage.realtime_start,
        realtime_end=vintage.realtime_end,
        published_at=published_at_from_vintage(vintage.realtime_start),
        fetched_at=fetched_at,
        source_record_key=_record_key(series.series_code, vintage),
        revision_no=revision_no,
    )


def _record_key(series_code: str, vintage: FredVintage) -> str:
    return (
        f"{series_code}|{vintage.observation_date.isoformat()}|{vintage.realtime_start.isoformat()}"
    )


def _renumber(session: Session, *, series_id: uuid.UUID, observation_date: date) -> None:
    rows = list(
        session.scalars(
            select(MacroObservation).where(
                MacroObservation.series_id == series_id,
                MacroObservation.observation_date == observation_date,
            )
        ).all()
    )
    rows.sort(key=_start_or_min)
    for index, row in enumerate(rows, start=1):
        row.revision_no = -index
    session.flush()
    for index, row in enumerate(rows, start=1):
        row.revision_no = index
    session.flush()


def _start_or_min(row: MacroObservation) -> date:
    if row.realtime_start is None:
        return date.min
    return row.realtime_start


def _seed_from_row(row: MacroSeries) -> MacroSeriesSeed:
    agency = row.metadata_json.get("source_agency")
    source_key = row.metadata_json.get("source_key")
    provider: MacroProvider = (
        "eastmoney" if row.metadata_json.get("provider") == "eastmoney" else "fred"
    )
    return MacroSeriesSeed(
        series_code=row.series_code,
        name_cn=row.name_cn,
        name_en=row.name_en,
        category=row.category,
        frequency=row.frequency,
        base_unit=row.base_unit,
        seasonal_adjustment=row.seasonal_adjustment,
        source_agency=agency if isinstance(agency, str) else "",
        provider=provider,
        source_key=source_key if isinstance(source_key, str) else "",
    )


def decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text
