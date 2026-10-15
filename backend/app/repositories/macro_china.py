import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import Engine, func, select, tuple_
from sqlalchemy.orm import Session

from app.db.models.macro import MacroObservation, MacroSeries
from app.db.models.provider import DataProvider
from app.services.china_macro import CsvObservation, CsvSeriesSpec, china_series_catalog
from app.utils.time import utc_now

CHINA_MACRO_PROVIDER_ID = uuid.uuid5(
    uuid.NAMESPACE_URL, "https://finscope.local/providers/china_macro"
)
CHINA_MACRO_PROVIDER_CODE = "china_macro"


@dataclass(frozen=True)
class StoredObservation:
    """一条已经落库的观测值。published_at 是公布时间，不是抓取时间。"""

    series_code: str
    observation_date: date
    value: Decimal
    unit: str
    source: str
    published_at: datetime
    fetched_at: datetime


@dataclass(frozen=True)
class SeriesStoredSummary:
    """按序列聚合的落库情况。observation_count 只统计真实存在的行。"""

    series_code: str
    observation_count: int
    latest: StoredObservation | None
    first_observation_date: date | None
    first_published_at: datetime | None


class MacroChinaRepository:
    """中国宏观序列的目录登记与观测值写入。表结构与 FRED 宏观共用一套。"""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def ensure_catalog(self) -> int:
        """登记六条中国序列。不写入任何观测值。"""

        specs = china_series_catalog()
        with Session(self.engine) as session:
            provider = _provider(session)
            for spec in specs:
                _upsert_series(session, provider_id=provider.id, spec=spec)
            session.commit()
        return len(specs)

    def import_observations(
        self,
        rows: Sequence[CsvObservation],
        *,
        fetched_at: datetime | None = None,
    ) -> tuple[int, list[str]]:
        """按序列+观测日+公布日幂等写入，返回（入库条数，重复明细）。"""

        self.ensure_catalog()
        moment = utc_now() if fetched_at is None else fetched_at
        accepted = 0
        duplicates: list[str] = []
        same_batch: set[tuple[str, date, date]] = set()
        with Session(self.engine) as session:
            series_by_code = {
                row.series_code: row
                for row in session.scalars(
                    select(MacroSeries).where(
                        MacroSeries.series_code.in_([row.series_code for row in rows])
                    )
                ).all()
            }
            existing = _existing_keys(session, rows)
            touched: set[tuple[uuid.UUID, date]] = set()
            for row in rows:
                key = (row.series_code, row.observation_date, row.published_at)
                series = series_by_code.get(row.series_code)
                if series is None:
                    duplicates.append(
                        f"{row.series_code} {row.observation_date.isoformat()} 未登记"
                    )
                    continue
                if key in same_batch or key in existing:
                    duplicates.append(
                        f"{row.series_code} {row.observation_date.isoformat()} "
                        f"{row.published_at.isoformat()} 已存在"
                    )
                    continue
                same_batch.add(key)
                session.add(
                    MacroObservation(
                        id=uuid.uuid4(),
                        series_id=series.id,
                        observation_date=row.observation_date,
                        value=row.value,
                        unit=row.unit,
                        realtime_start=row.published_at,
                        realtime_end=date.max,
                        published_at=datetime.combine(row.published_at, time.min),
                        fetched_at=moment,
                        source_record_key=_record_key(row),
                        revision_no=-1,
                    )
                )
                touched.add((series.id, row.observation_date))
                accepted += 1
            session.flush()
            for series_id, observation_date in touched:
                _renumber(session, series_id=series_id, observation_date=observation_date)
            session.commit()
        return accepted, duplicates

    def list_series_summary(self) -> list[SeriesStoredSummary]:
        """六条序列的已存条数与最新值。没有数据的序列计数为 0、latest 为 None。"""

        self.ensure_catalog()
        stored_codes = [spec.series_code for spec in china_series_catalog()]
        with Session(self.engine) as session:
            counts = dict(
                session.execute(
                    select(MacroSeries.series_code, func.count(MacroObservation.id))
                    .join(MacroObservation, MacroObservation.series_id == MacroSeries.id)
                    .where(MacroSeries.series_code.in_(stored_codes))
                    .group_by(MacroSeries.series_code)
                ).all()
            )
            latest_by_code = _latest_by_code(session, stored_codes)
            bounds = {
                code: (first, first_published)
                for code, first, first_published in session.execute(
                    select(
                        MacroSeries.series_code,
                        func.min(MacroObservation.observation_date),
                        func.min(MacroObservation.published_at),
                    )
                    .join(MacroObservation, MacroObservation.series_id == MacroSeries.id)
                    .where(MacroSeries.series_code.in_(stored_codes))
                    .group_by(MacroSeries.series_code)
                ).all()
            }
        summary: list[SeriesStoredSummary] = []
        for code in stored_codes:
            first, first_published = bounds.get(code, (None, None))
            summary.append(
                SeriesStoredSummary(
                    series_code=code,
                    observation_count=int(counts.get(code, 0)),
                    latest=latest_by_code.get(code),
                    first_observation_date=first,
                    first_published_at=first_published,
                )
            )
        return summary

    def count_since(self, start: date, end: date) -> int:
        """按观测日落库条数统计，供摘要使用。"""

        with Session(self.engine) as session:
            total = session.scalar(
                select(func.count(MacroObservation.id)).where(
                    MacroObservation.observation_date >= start,
                    MacroObservation.observation_date <= end,
                )
            )
        return int(total or 0)


def _provider(session: Session) -> DataProvider:
    found = session.scalars(
        select(DataProvider).where(DataProvider.code == CHINA_MACRO_PROVIDER_CODE)
    ).one_or_none()
    if found is not None:
        return found
    provider = DataProvider(
        id=CHINA_MACRO_PROVIDER_ID,
        code=CHINA_MACRO_PROVIDER_CODE,
        name="中国宏观正式来源 CSV",
        enabled=False,
        config_public={
            "transport": "manual_csv",
            "credential": "not_required",
            "note": "只接受官方来源导出的 CSV，不抓取页面",
        },
    )
    session.add(provider)
    session.flush()
    return provider


def _source_from_record(record_key: str) -> str:
    """source_record_key 里记录了导入时的来源名。"""

    parts = record_key.split("|")
    if len(parts) >= 4:
        return parts[3]
    return ""


def _upsert_series(session: Session, *, provider_id: uuid.UUID, spec: CsvSeriesSpec) -> None:
    existing = session.scalars(
        select(MacroSeries).where(MacroSeries.series_code == spec.series_code)
    ).one_or_none()
    metadata = _metadata(spec)
    if existing is None:
        session.add(
            MacroSeries(
                id=_series_id(spec.series_code),
                provider_id=provider_id,
                series_code=spec.series_code,
                name_cn=spec.name_cn,
                name_en=spec.name_en,
                region="CN",
                category=spec.category,
                frequency=spec.frequency,
                base_unit=spec.base_unit,
                seasonal_adjustment=spec.seasonal_adjustment,
                transform_rule="level",
                enabled=False,
                metadata_json=metadata,
            )
        )
        return
    existing.name_cn = spec.name_cn
    existing.name_en = spec.name_en
    existing.region = "CN"
    existing.category = spec.category
    existing.frequency = spec.frequency
    existing.base_unit = spec.base_unit
    existing.seasonal_adjustment = spec.seasonal_adjustment
    existing.transform_rule = "level"
    existing.metadata_json = metadata


def _series_id(series_code: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"https://finscope.local/china-series/{series_code}")


def _metadata(spec: CsvSeriesSpec) -> dict[str, object]:
    return {
        "official_title": spec.name_en,
        "unit_label": spec.base_unit,
        "seasonal_label": spec.seasonal_adjustment,
        "time_precision": "date",
        "source_agency": spec.source_agency,
        "source_url": "",
        "note": "数值只能由正式来源 CSV 导入，未导入时保持不可用",
    }


def _record_key(row: CsvObservation) -> str:
    """幂等键：序列 + 观测日 + 公布日。同一份 CSV 重放不会新增行。"""

    return "|".join(
        (
            row.series_code,
            row.observation_date.isoformat(),
            row.published_at.isoformat(),
            row.source,
            row.unit,
            format(row.value, "f"),
        )
    )


def _renumber(session: Session, *, series_id: uuid.UUID, observation_date: date) -> None:
    """同一观测日的多条修订按公布日排序重编号；先清空再写回，避免唯一约束冲突。"""

    rows = list(
        session.scalars(
            select(MacroObservation).where(
                MacroObservation.series_id == series_id,
                MacroObservation.observation_date == observation_date,
            )
        ).all()
    )
    rows.sort(key=_published_or_min)
    for index, row in enumerate(rows, start=1):
        row.revision_no = -index
    session.flush()
    for index, row in enumerate(rows, start=1):
        row.revision_no = index
    session.flush()


def _published_or_min(row: MacroObservation) -> datetime:
    if row.realtime_start is None:
        return datetime.min
    return datetime.combine(row.realtime_start, time.min)


def _existing_keys(session: Session, rows: Sequence[CsvObservation]) -> set[tuple[str, date, date]]:
    """一次性查出已存在的（序列，观测日，公布日），避免逐行查库。"""

    pairs = {(row.series_code, row.observation_date) for row in rows}
    if not pairs:
        return set()
    found = session.execute(
        select(
            MacroSeries.series_code,
            MacroObservation.observation_date,
            MacroObservation.realtime_start,
        )
        .join(MacroObservation, MacroObservation.series_id == MacroSeries.id)
        .where(
            tuple_(MacroSeries.series_code, MacroObservation.observation_date).in_(sorted(pairs))
        )
    ).all()
    return {
        (series_code, observation_date, realtime_start)
        for series_code, observation_date, realtime_start in found
        if realtime_start is not None
    }


def _latest_by_code(session: Session, series_codes: Sequence[str]) -> dict[str, StoredObservation]:
    """每条序列取观测日最新的一条；观测日相同时取公布时间最晚的那次修订。"""

    ranked = (
        select(
            MacroSeries.series_code.label("series_code"),
            MacroObservation.observation_date.label("observation_date"),
            MacroObservation.value.label("value"),
            MacroObservation.unit.label("unit"),
            MacroObservation.source_record_key.label("source_record_key"),
            MacroObservation.published_at.label("published_at"),
            MacroObservation.fetched_at.label("fetched_at"),
            func.row_number()
            .over(
                partition_by=MacroSeries.series_code,
                order_by=(
                    MacroObservation.observation_date.desc(),
                    MacroObservation.published_at.desc().nullslast(),
                    MacroObservation.revision_no.desc(),
                ),
            )
            .label("rank"),
        )
        .join(MacroObservation, MacroObservation.series_id == MacroSeries.id)
        .where(MacroSeries.series_code.in_(list(series_codes)))
        .subquery()
    )
    rows = session.execute(select(ranked).where(ranked.c.rank == 1)).all()
    latest: dict[str, StoredObservation] = {}
    for row in rows:
        latest[row.series_code] = StoredObservation(
            series_code=row.series_code,
            observation_date=row.observation_date,
            value=row.value,
            unit=row.unit,
            source=_source_from_record(row.source_record_key),
            published_at=row.published_at if row.published_at is not None else row.fetched_at,
            fetched_at=row.fetched_at,
        )
    return latest
