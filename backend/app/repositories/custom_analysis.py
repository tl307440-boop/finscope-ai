import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models.custom_analysis import CustomSeriesPoint, OptionDailyStat
from app.providers.exchange_options import OptionStat
from app.services.custom_analysis_rules import OptionRow, QualityResult

# 参数个数上限留足余量：一次 upsert 的行数太多会撞 PostgreSQL 的 65535 参数上限。
_BATCH = 2000


@dataclass(frozen=True)
class SeriesPointIn:
    trade_date: date
    value: Decimal
    source: str
    quality_flag: str
    meta: dict[str, Any] | None = None


@dataclass(frozen=True)
class StoredPoint:
    trade_date: date
    value: Decimal
    quality_flag: str
    meta: dict[str, Any] | None


@dataclass(frozen=True)
class SeriesSummary:
    series_code: str
    rows: int
    first_date: date
    last_date: date
    review_rows: int
    fetched_at: datetime
    source: str


@dataclass(frozen=True)
class OptionSummary:
    exchange: str
    rows: int
    first_date: date
    last_date: date
    review_rows: int
    fetched_at: datetime
    source: str


class CustomAnalysisRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # ------------------------------------------------------------ 期权
    def upsert_option_stats(
        self,
        rows: Sequence[tuple[OptionStat, QualityResult]],
        fetched_at: datetime,
    ) -> int:
        values = [
            {
                "id": uuid.uuid4(),
                "trade_date": stat.trade_date,
                "exchange": stat.exchange,
                "underlying_code": stat.underlying_code,
                "underlying_name": stat.underlying_name,
                "call_volume": stat.call_volume,
                "put_volume": stat.put_volume,
                "call_oi": stat.call_oi,
                "put_oi": stat.put_oi,
                "reported_ratio": stat.reported_ratio,
                "reported_ratio_kind": stat.reported_ratio_kind,
                "quality_flag": quality.flag,
                "quality_note": quality.note,
                "source": stat.source,
                "fetched_at": fetched_at,
            }
            for stat, quality in rows
            if quality.flag != "rejected"
        ]
        if not values:
            return 0
        updated = (
            "underlying_name",
            "call_volume",
            "put_volume",
            "call_oi",
            "put_oi",
            "reported_ratio",
            "reported_ratio_kind",
            "quality_flag",
            "quality_note",
            "source",
            "fetched_at",
        )
        with Session(self.engine) as session, session.begin():
            for start in range(0, len(values), _BATCH):
                statement = insert(OptionDailyStat).values(values[start : start + _BATCH])
                statement = statement.on_conflict_do_update(
                    constraint="uq_option_daily_stats_identity",
                    set_={column: statement.excluded[column] for column in updated},
                )
                session.execute(statement)
        return len(values)

    def option_days(self, exchange: str) -> set[date]:
        with Session(self.engine) as session:
            rows = session.execute(
                select(OptionDailyStat.trade_date)
                .where(OptionDailyStat.exchange == exchange)
                .distinct()
            ).scalars()
            return set(rows)

    def option_rows(
        self, members: Sequence[tuple[str, str]], start: date | None = None
    ) -> list[OptionRow]:
        if not members:
            return []
        statement = (
            select(OptionDailyStat)
            .where(tuple_(OptionDailyStat.exchange, OptionDailyStat.underlying_code).in_(members))
            .order_by(OptionDailyStat.trade_date)
        )
        if start is not None:
            statement = statement.where(OptionDailyStat.trade_date >= start)
        with Session(self.engine) as session:
            return [
                OptionRow(
                    trade_date=row.trade_date,
                    exchange=row.exchange,
                    underlying_code=row.underlying_code,
                    call_volume=row.call_volume,
                    put_volume=row.put_volume,
                    call_oi=row.call_oi,
                    put_oi=row.put_oi,
                    quality_flag=row.quality_flag,
                )
                for row in session.execute(statement).scalars()
            ]

    def option_summaries(self) -> list[OptionSummary]:
        with Session(self.engine) as session:
            statement = select(
                OptionDailyStat.exchange,
                func.count(),
                func.min(OptionDailyStat.trade_date),
                func.max(OptionDailyStat.trade_date),
                func.count().filter(OptionDailyStat.quality_flag != "ok"),
                func.max(OptionDailyStat.fetched_at),
                func.max(OptionDailyStat.source),
            ).group_by(OptionDailyStat.exchange)
            return [
                OptionSummary(
                    exchange=exchange,
                    rows=rows,
                    first_date=first,
                    last_date=last,
                    review_rows=review,
                    fetched_at=fetched,
                    source=source,
                )
                for exchange, rows, first, last, review, fetched, source in session.execute(
                    statement
                )
            ]

    def option_review_notes(self, limit: int = 20) -> list[tuple[date, str, str, str]]:
        with Session(self.engine) as session:
            statement = (
                select(
                    OptionDailyStat.trade_date,
                    OptionDailyStat.exchange,
                    OptionDailyStat.underlying_code,
                    OptionDailyStat.quality_note,
                )
                .where(OptionDailyStat.quality_flag != "ok")
                .order_by(OptionDailyStat.trade_date.desc())
                .limit(limit)
            )
            return [
                (day, exchange, code, note or "")
                for day, exchange, code, note in session.execute(statement)
            ]

    # ------------------------------------------------------------ 序列
    def upsert_points(
        self, series_code: str, points: Sequence[SeriesPointIn], fetched_at: datetime
    ) -> int:
        if not points:
            return 0
        values = [
            {
                "id": uuid.uuid4(),
                "series_code": series_code,
                "trade_date": point.trade_date,
                "value": point.value,
                "meta": point.meta,
                "source": point.source,
                "quality_flag": point.quality_flag,
                "fetched_at": fetched_at,
            }
            for point in points
        ]
        with Session(self.engine) as session, session.begin():
            for start in range(0, len(values), _BATCH):
                statement = insert(CustomSeriesPoint).values(values[start : start + _BATCH])
                statement = statement.on_conflict_do_update(
                    constraint="uq_custom_series_points_identity",
                    set_={
                        column: statement.excluded[column]
                        for column in ("value", "meta", "source", "quality_flag", "fetched_at")
                    },
                )
                session.execute(statement)
        return len(values)

    def series_dates(self, series_code: str) -> set[date]:
        with Session(self.engine) as session:
            rows = session.execute(
                select(CustomSeriesPoint.trade_date).where(
                    CustomSeriesPoint.series_code == series_code
                )
            ).scalars()
            return set(rows)

    def series(self, series_code: str, start: date | None = None) -> list[StoredPoint]:
        statement = (
            select(CustomSeriesPoint)
            .where(CustomSeriesPoint.series_code == series_code)
            .order_by(CustomSeriesPoint.trade_date)
        )
        if start is not None:
            statement = statement.where(CustomSeriesPoint.trade_date >= start)
        with Session(self.engine) as session:
            return [
                StoredPoint(
                    trade_date=row.trade_date,
                    value=row.value,
                    quality_flag=row.quality_flag,
                    meta=row.meta,
                )
                for row in session.execute(statement).scalars()
            ]

    def series_review_notes(self, limit: int = 20) -> list[tuple[date, str, str]]:
        with Session(self.engine) as session:
            statement = (
                select(
                    CustomSeriesPoint.trade_date,
                    CustomSeriesPoint.series_code,
                    CustomSeriesPoint.meta,
                )
                .where(CustomSeriesPoint.quality_flag != "ok")
                .order_by(CustomSeriesPoint.trade_date.desc())
                .limit(limit)
            )
            return [
                (day, code, str((meta or {}).get("note") or ""))
                for day, code, meta in session.execute(statement)
            ]

    def series_summaries(self) -> dict[str, SeriesSummary]:
        with Session(self.engine) as session:
            statement = select(
                CustomSeriesPoint.series_code,
                func.count(),
                func.min(CustomSeriesPoint.trade_date),
                func.max(CustomSeriesPoint.trade_date),
                func.count().filter(CustomSeriesPoint.quality_flag != "ok"),
                func.max(CustomSeriesPoint.fetched_at),
                func.max(CustomSeriesPoint.source),
            ).group_by(CustomSeriesPoint.series_code)
            return {
                code: SeriesSummary(
                    series_code=code,
                    rows=rows,
                    first_date=first,
                    last_date=last,
                    review_rows=review,
                    fetched_at=fetched,
                    source=source,
                )
                for code, rows, first, last, review, fetched, source in session.execute(statement)
            }
