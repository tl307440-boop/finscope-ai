"""全站数据新鲜度：宏观序列、日线、各类同步任务与后台进程心跳。

只读已有的表，不另建状态：宏观与日线看「数据日期」，同步任务看「最近一次成功」，
进程看心跳。阈值见 :mod:`app.services.freshness`。
"""

from datetime import datetime, timedelta

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models.macro import MacroObservation, MacroSeries
from app.db.models.market import Instrument, MarketBar
from app.db.models.provider import SyncJob
from app.db.models.worker_heartbeat import WorkerHeartbeat
from app.schemas.freshness import FreshnessItem, FreshnessReport, ItemState
from app.services import freshness

# 每类任务的展示名与新鲜度阈值（按「最近一次成功」算）。
_JOBS: tuple[tuple[str, str, freshness.Threshold], ...] = (
    ("fred_series", "美国宏观（FRED）同步", freshness.FINANCIALS),
    ("akshare_daily", "A 股日线同步", freshness.BARS),
    ("tiingo_daily", "美股日线同步", freshness.BARS),
    ("sec_filings", "美股财报同步（SEC）", freshness.FINANCIALS),
    ("cn_statements", "A 股财报同步（东方财富）", freshness.FINANCIALS),
    ("china_macro", "中国宏观六序列同步（东方财富 / 商务部）", freshness.FINANCIALS),
    ("financial_metrics", "财务体检快照（本地计算）", freshness.FINANCIALS),
    ("macro_regime", "宏观状态判断快照（本地计算）", freshness.FINANCIALS),
    ("hotspot_events", "热点披露刷新（巨潮 / SEC）", freshness.FINANCIALS),
    ("us_universe", "美股证券清单（SEC）", freshness.UNIVERSE),
    ("cn_universe", "A 股证券清单（巨潮）", freshness.UNIVERSE),
)
# 调度器心跳超过这个时间没更新，就认为它没在跑（它每秒写一次）。
_HEARTBEAT_TTL = timedelta(seconds=60)


def build_report(engine: Engine, settings: Settings, now: datetime) -> FreshnessReport:
    items: list[FreshnessItem] = []
    scheduler_running = _heartbeats(engine, settings, now, items)
    items.extend(_macro(engine, now))
    items.extend(_bars(engine, settings, now))
    items.extend(_jobs(engine, settings, now))
    return FreshnessReport(generated_at=now, scheduler_running=scheduler_running, items=items)


def _heartbeats(
    engine: Engine, settings: Settings, now: datetime, items: list[FreshnessItem]
) -> bool:
    with Session(engine) as session:
        rows = {row.service_name: row for row in session.scalars(select(WorkerHeartbeat)).all()}
    running = False
    for name, label in (
        (settings.worker_service_name, "任务执行器（worker）"),
        ("scheduler", "定时调度器"),
    ):
        row = rows.get(name)
        if row is None:
            state: ItemState = "unavailable"
            detail = "从未启动，没有心跳记录"
            beat = None
        else:
            alive = row.status == "alive" and now - row.beat_at <= _HEARTBEAT_TTL
            state = "fresh" if alive else "stale"
            detail = "运行中" if alive else "已停止或心跳超时，定时抓取不会执行"
            beat = row.beat_at
        if name == "scheduler":
            running = state == "fresh"
        items.append(
            FreshnessItem(
                group="process",
                key=name,
                label=label,
                state=state,
                detail=detail,
                last_synced_at=beat,
            )
        )
    return running


def _macro(engine: Engine, now: datetime) -> list[FreshnessItem]:
    with Session(engine) as session:
        rows = session.execute(
            select(
                MacroSeries.series_code,
                MacroSeries.name_cn,
                MacroSeries.frequency,
                func.max(MacroObservation.observation_date),
                func.max(MacroObservation.fetched_at),
            )
            .outerjoin(MacroObservation, MacroObservation.series_id == MacroSeries.id)
            .group_by(MacroSeries.series_code, MacroSeries.name_cn, MacroSeries.frequency)
            .order_by(MacroSeries.series_code)
        ).all()
    items: list[FreshnessItem] = []
    for code, name, frequency, latest, fetched in rows:
        state: ItemState = freshness.day_state(
            latest, now.date(), freshness.macro_threshold(frequency)
        )
        detail = f"{frequency}序列" if latest else "尚无观测值（还没有同步到数据）"
        items.append(
            FreshnessItem(
                group="macro",
                key=code,
                label=name,
                state="unavailable" if state == "missing" else state,
                detail=detail,
                latest_data_date=latest,
                last_synced_at=fetched,
            )
        )
    return items


def _bars(engine: Engine, settings: Settings, now: datetime) -> list[FreshnessItem]:
    items: list[FreshnessItem] = []
    for market, label in (("CN", "A 股日线"), ("US", "美股日线")):
        with Session(engine) as session:
            latest_per = (
                select(MarketBar.instrument_id, func.max(MarketBar.trade_date).label("last"))
                .join(Instrument, Instrument.id == MarketBar.instrument_id)
                .where(Instrument.market == market)
                .group_by(MarketBar.instrument_id)
                .subquery()
            )
            rows = session.execute(select(latest_per.c.last)).all()
        dates = [row[0] for row in rows]
        if market == "US" and not dates and not settings.tiingo_api_key.strip():
            items.append(
                FreshnessItem(
                    group="bars",
                    key="us_bars",
                    label=label,
                    state="unavailable",
                    detail="未配置 TIINGO_API_KEY，美股日线不可用",
                )
            )
            continue
        latest = max(dates, default=None)
        stale = sum(
            1 for day in dates if freshness.day_state(day, now.date(), freshness.BARS) == "stale"
        )
        state: ItemState = freshness.day_state(latest, now.date(), freshness.BARS)
        items.append(
            FreshnessItem(
                group="bars",
                key=f"{market.lower()}_bars",
                label=label,
                state="unavailable" if state == "missing" else state,
                detail=f"{len(dates)} 个标的有日线，其中 {stale} 个已过期",
                latest_data_date=latest,
            )
        )
    return items


def _jobs(engine: Engine, settings: Settings, now: datetime) -> list[FreshnessItem]:
    since = now - timedelta(hours=24)
    items: list[FreshnessItem] = []
    with Session(engine) as session:
        for job_type, label, threshold in _JOBS:
            success = session.scalar(
                select(func.max(SyncJob.finished_at)).where(
                    SyncJob.job_type == job_type, SyncJob.status == "succeeded"
                )
            )
            failed = (
                session.scalar(
                    select(func.count())
                    .select_from(SyncJob)
                    .where(
                        SyncJob.job_type == job_type,
                        SyncJob.status == "failed",
                        SyncJob.finished_at >= since,
                    )
                )
                or 0
            )
            active = (
                session.scalar(
                    select(func.count())
                    .select_from(SyncJob)
                    .where(
                        SyncJob.job_type == job_type,
                        SyncJob.status.in_(("queued", "running")),
                    )
                )
                or 0
            )
            if job_type == "tiingo_daily" and not settings.tiingo_api_key.strip():
                state: ItemState = "unavailable"
                detail = "未配置 TIINGO_API_KEY"
            else:
                base = freshness.sync_state(success, now, threshold)
                state = "unavailable" if base == "missing" else base
                detail = f"排队/执行中 {active} 条，近 24 小时失败 {failed} 条"
                if base == "missing":
                    detail = "还没有成功的同步记录；" + detail
            items.append(
                FreshnessItem(
                    group="sync",
                    key=job_type,
                    label=label,
                    state=state,
                    detail=detail,
                    last_synced_at=success,
                )
            )
    return items
