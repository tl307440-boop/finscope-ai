"""任务调度（规格书 §11「任务调度设计」）。

调度器**只负责创建任务**：每个 job 被触发时只做一件事——按幂等标识往
``sync_jobs`` 写一条 ``queued`` 记录（或命中已有记录时返回 ``created=False``），
真正的取任务、加锁、续租、重试都在 worker 里（``SELECT ... FOR UPDATE SKIP LOCKED``）。
调度器绝不直接抓取或解析数据，这样同一份工作不会因为调度和执行两处都在跑而重复处理。

规格书 §11 的 7 个任务与幂等标识的对应（``TASK_REGISTRY`` 里逐条登记）：

======================  ================================  ==================================
任务                    幂等标识                          本实现的 dedupe_key
======================  ================================  ==================================
sync_fred_series        provider+series+date              ``fred:{series_code}:{date}``
sync_a_stock_bars       provider+instrument+trade_date+adjustment
                                                          ``akshare:{symbol}:{adjustment}:{trade_date}``
sync_us_sec_filings     company+filing accession          ``sec:{provider}:{cik}:{day}``
parse_document          sha256+parser_version             ``parse_document:{sha256}:{version}``
compute_financial_metrics
                        instrument+period+metric_version  ``metrics:{symbol}:{period}:{version}``
compute_macro_regime    as_of+model_version               ``macro_regime:{as_of}:{version}``
refresh_hotspot_events  date+source                       ``hotspot:{source}:{date}``
======================  ================================  ==================================

幂等为什么真的生效（两层，缺一不可）：

1. 数据库层：迁移 ``20261015_0005`` 已经建了**部分唯一索引** ``uq_sync_jobs_active_dedupe``
   （``WHERE status IN ('queued','running')``）。重复插入会撞索引，所以即使两个调度进程
   同时触发同一 keys，数据库也只会留下一行。
2. 应用层：``SyncJobRepository.enqueue`` 先在同一事务里 ``_active`` 查一次（先查后插），
   命中就返回 ``created=False`` 并复用原记录，避免把唯一冲突当异常抛给调度线程。

两者叠加的好处是：键里带日期/报告期的任务，历史上成功过的记录不会被再次创建，而失败的
历史记录保留在表里（部分唯一索引只管活跃状态），符合「保留历史失败记录」。
"""

import logging
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol
from uuid import UUID

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.db.models.document import Document
from app.db.models.provider import DataProvider
from app.providers.akshare_bars import DAILY_TARGETS, AdjustmentCode, target_for
from app.repositories.instruments import InstrumentRepository
from app.repositories.macro import MacroRepository
from app.repositories.macro_china import MacroChinaRepository, SeriesStoredSummary
from app.repositories.market_bars import MarketBarRepository
from app.repositories.sync_jobs import JobSnapshot, SyncJobRepository
from app.repositories.universe import UniverseRepository
from app.services.china_macro import china_series_catalog
from app.services.data_sync import bars_window
from app.services.pdf_upload import default_parser_version, parse_document_identity
from app.services.runtime_settings import effective_settings
from app.services.sync_queue import dedupe_key_for
from app.utils.time import utc_now

logger = logging.getLogger(__name__)

# 幂等键里带日期时用的后缀，避免同一任务在一天内被重复登记。
DATE_KEY_FORMAT = "%Y-%m-%d"
# 默认只给 A 股做复权方式为不复权的日线；与 sync_queue.dedupe_key_for 的既有键保持一致。
DEFAULT_ADJUSTMENT: AdjustmentCode = "none"
# 计算类任务的版本号；改了算法就改这里，旧报告不会被覆盖（规格书 §11「不覆盖旧报告」）。
METRIC_VERSION = "v1"
MACRO_REGIME_MODEL_VERSION = "v1"
# 交易日判断往回找的最大天数，找不到就按不可用处理，不猜。
MAX_TRADING_DAY_LOOKBACK = 30
FRED_PROVIDER_ID = UUID("a1b2c3d4-0000-4000-8000-000000000101")


class SchedulerConfigurationError(AppError):
    """调度配置不可用；消息面向运维，不含密钥。"""

    def __init__(self, message: str) -> None:
        super().__init__(code="SCHEDULER_UNAVAILABLE", message=message, status_code=503)


@dataclass(frozen=True)
class ScheduledTask:
    """一条待登记的任务：幂等键 + 任务范围 + 失败策略。"""

    task_name: str
    job_type: str
    dedupe_key: str
    scope: dict[str, object]
    provider_id: UUID
    failure_policy: str
    max_attempts: int = 3
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class RegisteredJob:
    """已登记的 APScheduler job，供启动日志与测试断言使用。"""

    job_id: str
    task_name: str
    trigger: str
    failure_policy: str


@dataclass(frozen=True)
class TriggerRegistration:
    """一个 APScheduler 触发器的声明；时间/开关全部来自 Settings。"""

    job_id: str
    task_name: str
    trigger: CronTrigger
    builder: Callable[[Engine, Settings, datetime], Sequence[ScheduledTask]]
    failure_policy: str


class Enqueuer(Protocol):
    """登记任务的边界；测试可以换成假实现，不必连数据库。"""

    def enqueue(
        self,
        *,
        job_type: str,
        provider_id: UUID,
        scope: dict[str, object],
        dedupe_key: str,
        requested_by: UUID | None,
        now: datetime,
        max_attempts: int = 3,
    ) -> tuple[JobSnapshot, bool]: ...


def _cron(hour: int, minute: int, *, day_of_week: str | None = None) -> CronTrigger:
    if day_of_week is None:
        return CronTrigger(hour=hour, minute=minute)
    return CronTrigger(day_of_week=day_of_week, hour=hour, minute=minute)


def build_triggers(settings: Settings) -> list[TriggerRegistration]:
    """把 Settings 里的开关/时间/时区翻成触发计划；关闭的 job 不登记。"""

    zone = settings.scheduler_timezone
    triggers: list[TriggerRegistration] = []
    if settings.scheduler_enable_fred:
        triggers.append(
            TriggerRegistration(
                job_id="sync_fred_series",
                task_name="sync_fred_series",
                trigger=CronTrigger(
                    hour=settings.scheduler_fred_hour,
                    minute=settings.scheduler_fred_minute,
                    timezone=zone,
                ),
                builder=build_fred_tasks,
                failure_policy="超时/429 退避，保留上次已入库的值（不写入空值覆盖）",
            )
        )
    if settings.scheduler_enable_a_stock_bars:
        # A 股日线：收盘后延迟执行，且只在交易日登记。
        close_hour, close_minute = _shift_after_close(
            settings.scheduler_a_stock_close_delay_minutes
        )
        triggers.append(
            TriggerRegistration(
                job_id="sync_a_stock_bars",
                task_name="sync_a_stock_bars",
                trigger=CronTrigger(
                    day_of_week="mon-fri",
                    hour=close_hour,
                    minute=close_minute,
                    timezone=zone,
                ),
                builder=build_a_stock_tasks,
                failure_policy="最多重试 3 次",
            )
        )
    if settings.scheduler_enable_us_sec_filings:
        for index, (hour, minute) in enumerate(settings.scheduler_sec_slots()):
            triggers.append(
                TriggerRegistration(
                    job_id=f"sync_us_sec_filings_{index + 1}",
                    task_name="sync_us_sec_filings",
                    trigger=CronTrigger(hour=hour, minute=minute, timezone=zone),
                    builder=build_sec_tasks,
                    failure_policy="遵守 SEC 公平访问政策：串行、限速、带 UA，不做并发抓取",
                )
            )
    if settings.scheduler_enable_financial_metrics:
        triggers.append(
            TriggerRegistration(
                job_id="compute_financial_metrics",
                task_name="compute_financial_metrics",
                trigger=CronTrigger(
                    hour=settings.scheduler_metrics_hour,
                    minute=settings.scheduler_metrics_minute,
                    timezone=zone,
                ),
                builder=build_financial_metrics_tasks,
                failure_policy="缺数据仅部分生成，其余指标留空并标 unavailable",
            )
        )
    if settings.scheduler_enable_macro_regime:
        triggers.append(
            TriggerRegistration(
                job_id="compute_macro_regime",
                task_name="compute_macro_regime",
                trigger=CronTrigger(
                    hour=settings.scheduler_macro_regime_hour,
                    minute=settings.scheduler_macro_regime_minute,
                    timezone=zone,
                ),
                builder=build_macro_regime_tasks,
                failure_policy="按 as_of 生成新报告，不覆盖旧报告",
            )
        )
    if settings.scheduler_enable_hotspot_events:
        triggers.append(
            TriggerRegistration(
                job_id="refresh_hotspot_events",
                task_name="refresh_hotspot_events",
                trigger=CronTrigger(
                    hour=settings.scheduler_hotspot_hour,
                    minute=settings.scheduler_hotspot_minute,
                    timezone=zone,
                ),
                builder=build_hotspot_tasks,
                failure_policy="来源无授权直接禁用，不生成任务",
            )
        )
    if settings.scheduler_enable_cn_statements:
        triggers.append(
            TriggerRegistration(
                job_id="sync_cn_statements",
                task_name="sync_cn_statements",
                trigger=CronTrigger(
                    hour=settings.scheduler_cn_statements_hour,
                    minute=settings.scheduler_cn_statements_minute,
                    timezone=zone,
                ),
                builder=build_cn_statement_tasks,
                failure_policy="只刷新被关注过的 A 股；最多重试 2 次，失败保留上次入库的值",
            )
        )
    if settings.scheduler_enable_us_bars:
        triggers.append(
            TriggerRegistration(
                job_id="sync_us_bars",
                task_name="sync_us_bars",
                trigger=CronTrigger(
                    day_of_week="tue-sat",
                    hour=settings.scheduler_us_bars_hour,
                    minute=settings.scheduler_us_bars_minute,
                    timezone=zone,
                ),
                builder=build_us_bar_tasks,
                failure_policy="Tiingo 限额：每轮限量、轮流刷新；无 key 标 unavailable",
            )
        )
    if settings.scheduler_enable_china_macro:
        triggers.append(
            TriggerRegistration(
                job_id="sync_china_macro",
                task_name="sync_china_macro",
                trigger=CronTrigger(
                    hour=settings.scheduler_china_macro_hour,
                    minute=settings.scheduler_china_macro_minute,
                    timezone=zone,
                ),
                builder=build_china_macro_tasks,
                failure_policy="六个序列分源抓取；部分失败仍写入成功的，整体按重试策略重来",
            )
        )
    if settings.scheduler_enable_board_market:
        triggers.append(
            TriggerRegistration(
                job_id="sync_board_market",
                task_name="sync_board_market",
                trigger=CronTrigger(
                    day_of_week="mon-fri",
                    hour=settings.scheduler_board_market_hour,
                    minute=settings.scheduler_board_market_minute,
                    timezone=zone,
                ),
                builder=build_board_market_tasks,
                failure_policy="非交易日不写快照；连续 3 次上游失败即停，最多重试 3 次",
            )
        )
    if settings.scheduler_enable_custom_analysis:
        triggers.append(
            TriggerRegistration(
                job_id="sync_custom_analysis",
                task_name="sync_custom_analysis",
                trigger=CronTrigger(
                    day_of_week="mon-fri",
                    hour=settings.scheduler_custom_analysis_hour,
                    minute=settings.scheduler_custom_analysis_minute,
                    timezone=zone,
                ),
                builder=build_custom_analysis_tasks,
                failure_policy="官方源逐日增量；连续 3 次上游失败即停，已写入的保留，最多重试 3 次",
            )
        )
    if settings.scheduler_enable_universe:
        triggers.append(
            TriggerRegistration(
                job_id="sync_universe",
                task_name="sync_universe",
                trigger=CronTrigger(
                    day_of_week="sun",
                    hour=settings.scheduler_universe_hour,
                    minute=settings.scheduler_universe_minute,
                    timezone=zone,
                ),
                builder=build_universe_tasks,
                failure_policy="每周一次；失败保留上次清单，不删除已有证券",
            )
        )
    if settings.scheduler_enable_parse_document:
        # 上传后立即入队是主路径（见 parse_document_task）；这里只做兜底扫描，
        # 把「已落盘但没进队列」的文档补登记，撞上同一 sha256+parser_version 会被幂等键拦掉。
        triggers.append(
            TriggerRegistration(
                job_id="parse_document",
                task_name="parse_document",
                trigger=IntervalTrigger(
                    seconds=settings.scheduler_parse_sweep_seconds, timezone=zone
                ),
                builder=build_parse_document_tasks,
                failure_policy="格式失败写状态，不无限重试",
            )
        )
    return triggers


def _shift_after_close(delay_minutes: int) -> tuple[int, int]:
    """收盘时间（本地 15:00）+ 延迟；跨天时退回 23:59，避免触发时间落到当天之外。"""

    close = time(hour=15, minute=0)
    shifted = (datetime.combine(date(2000, 1, 1), close) + timedelta(minutes=delay_minutes)).time()
    if shifted <= close and delay_minutes > 0:
        return 23, 59
    return shifted.hour, shifted.minute


def register_jobs(
    scheduler: BackgroundScheduler,
    settings: Settings,
    *,
    run_on_startup: bool | None = None,
) -> list[RegisteredJob]:
    """把 7 个任务登记成 job；返回真实登记结果，便于启动日志与自检。"""

    immediately = settings.scheduler_run_on_startup if run_on_startup is None else run_on_startup
    registered: list[RegisteredJob] = []
    for plan in build_triggers(settings):
        scheduler.add_job(
            enqueue_tasks,
            trigger=plan.trigger,
            id=plan.job_id,
            name=plan.task_name,
            kwargs={"builder": plan.builder, "task_name": plan.task_name},
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=settings.scheduler_misfire_grace_seconds,
        )
        registered.append(
            RegisteredJob(
                job_id=plan.job_id,
                task_name=plan.task_name,
                trigger=str(plan.trigger),
                failure_policy=plan.failure_policy,
            )
        )
        if immediately:
            scheduler.add_job(
                enqueue_tasks,
                kwargs={"builder": plan.builder, "task_name": plan.task_name},
                id=f"{plan.job_id}__startup",
                name=f"{plan.task_name}（启动即跑一次）",
                replace_existing=True,
                max_instances=1,
            )
    return registered


UNIVERSE_MIN_US = 1000


def catch_up_universe(engine: Engine, settings: Settings) -> int:
    """首次启动时清单还没灌（美股只有种子数据）就补登记一次，不用等到周日。"""

    if not settings.scheduler_enable_universe:
        return 0
    if UniverseRepository(engine).count("US") >= UNIVERSE_MIN_US:
        return 0
    return enqueue_built(engine, settings, build_universe_tasks, "sync_universe")


def china_macro_missing_series(summaries: Sequence[SeriesStoredSummary]) -> list[str]:
    """自动同步的序列里一条观测值都没有的。人工导入的序列（人民银行三项）不算：
    它们本来就不会被定时任务抓取，补登记也补不上。"""

    manual = {spec.series_code for spec in china_series_catalog() if spec.manual_only}
    return [
        item.series_code
        for item in summaries
        if item.observation_count == 0 and item.series_code not in manual
    ]


def catch_up_china_macro(
    engine: Engine,
    settings: Settings,
    *,
    summaries: Sequence[SeriesStoredSummary] | None = None,
    builder: Callable[[Engine, Settings, datetime], Sequence[ScheduledTask]] | None = None,
    enqueuer: Enqueuer | None = None,
) -> int:
    """启动时发现有自动序列一条数据都没有（比如新增了序列、刚部署完），就补登记一次抓取，
    不用等到次日 01:20。登记键按天幂等，同一天重启多次也只会有一条。"""

    if not settings.scheduler_enable_china_macro:
        return 0
    stored = (
        summaries if summaries is not None else MacroChinaRepository(engine).list_series_summary()
    )
    missing = china_macro_missing_series(stored)
    if not missing:
        return 0
    logger.info("scheduler china macro catch-up missing=%s", ",".join(missing))
    return enqueue_built(
        engine,
        settings,
        builder if builder is not None else build_china_macro_tasks,
        "sync_china_macro",
        enqueuer=enqueuer,
    )


def enqueue_tasks(
    builder: Callable[[Engine, Settings, datetime], Sequence[ScheduledTask]],
    task_name: str,
) -> int:
    """触发器回调：建引擎、按幂等键登记 queued 记录，然后立刻释放连接。"""

    from app.db.session import create_db_engine

    base = get_settings()
    engine = create_db_engine(base.database_url)
    try:
        # 每次触发都重新读库：界面里刚填的密钥下一次定时就生效。
        settings = effective_settings(engine, base, use_cache=False)
        return enqueue_built(engine, settings, builder, task_name)
    finally:
        engine.dispose()


def enqueue_built(
    engine: Engine,
    settings: Settings,
    builder: Callable[[Engine, Settings, datetime], Sequence[ScheduledTask]],
    task_name: str,
    *,
    enqueuer: Enqueuer | None = None,
) -> int:
    """登记一批任务；返回本次真正新建的 queued 记录数（幂等命中记 0）。"""

    now = utc_now()
    target: Enqueuer = enqueuer if enqueuer is not None else SyncJobRepository(engine)
    created_counts = 0
    for task in builder(engine, settings, now):
        if task.unavailable_reason is not None:
            # 外部依赖不可用时标 unavailable，绝不写一条注定假成功的记录。
            logger.warning(
                "scheduler task unavailable task=%s reason=%s",
                task.task_name,
                task.unavailable_reason,
            )
            continue
        job, created = target.enqueue(
            job_type=task.job_type,
            provider_id=task.provider_id,
            scope=dict(task.scope),
            dedupe_key=task.dedupe_key,
            requested_by=None,
            now=now,
            max_attempts=task.max_attempts,
        )
        created_counts += int(created)
        logger.info(
            "scheduler enqueue task=%s job=%s dedupe=%s created=%s status=%s",
            task.task_name,
            job.id,
            task.dedupe_key,
            created,
            job.status,
        )
    logger.info("scheduler tick done task=%s created=%s", task_name, created_counts)
    return created_counts


# --------------------------------------------------------------------------- 任务登记


def build_fred_tasks(engine: Engine, settings: Settings, now: datetime) -> list[ScheduledTask]:
    """每天 1 次宏观序列同步；没有密钥时执行器走 FRED 公开 CSV（只有最新修订）。"""

    MacroRepository(engine).ensure_catalog()
    provider_id = _provider_id(engine, code="fred", name="FRED")
    end = now.date()
    start = settings.scheduler_fred_start(end)
    tasks: list[ScheduledTask] = []
    for series_code in settings.scheduler_series_codes():
        scope: dict[str, object] = {
            "series_code": series_code,
            "observation_start": start.isoformat(),
            "observation_end": end.isoformat(),
        }
        tasks.append(
            ScheduledTask(
                task_name="sync_fred_series",
                job_type="fred_series",
                # 幂等标识：provider + series + date
                dedupe_key=dedupe_key_for("fred_series", _as_text_scope(scope)),
                scope=scope,
                provider_id=provider_id,
                failure_policy="超时/429 退避，保留上次已入库的值",
            )
        )
    return tasks


def build_a_stock_tasks(engine: Engine, settings: Settings, now: datetime) -> list[ScheduledTask]:
    """A 股日线：只补「被关注过」的标的，从最后一根日线的次日补到今天。

    不再依赖交易日历判断：日历只记录了开市日，周末与休市日是「未知」，按未知跳过会让
    整条链路在日历用完后静默停摆。改为请求增量窗口——休市日上游返回空，worker 当作
    「没有新交易日」成功收尾；真正的断更由新鲜度监控按最后一根日线报出来。
    """

    repository = UniverseRepository(engine)
    today = now.date()
    tasks: list[ScheduledTask] = []
    for company in repository.tracked_cn_bars():
        if target_for(company.display_symbol) is None:
            continue
        window = bars_window(repository.last_bar_date(company.instrument_id), today)
        if window is None:
            continue
        tasks.append(cn_bars_task(engine, company.display_symbol, window[0], window[1]))
    return tasks


def cn_bars_task(engine: Engine, symbol: str, start: date, end: date) -> ScheduledTask:
    provider_id = MarketBarRepository(engine).ensure_akshare_provider()
    scope: dict[str, object] = {
        "display_symbol": symbol,
        "observation_start": start.isoformat(),
        "observation_end": end.isoformat(),
        "adjustment": DEFAULT_ADJUSTMENT,
    }
    return ScheduledTask(
        task_name="sync_a_stock_bars",
        job_type="akshare_daily",
        # 幂等标识：provider + instrument + 窗口 + adjustment
        dedupe_key=dedupe_key_for("akshare_daily", _as_text_scope(scope)),
        scope=scope,
        provider_id=provider_id,
        failure_policy="最多重试 3 次",
        max_attempts=3,
    )


def build_sec_tasks(engine: Engine, settings: Settings, now: datetime) -> list[ScheduledTask]:
    """SEC 财报：只刷新被关注过的美股，由 worker 串行执行以遵守公平访问政策。"""

    day = now.date().isoformat()
    return [
        sec_filings_task(engine, company.cik, company.display_symbol, day)
        for company in UniverseRepository(engine).tracked_us()
        if company.cik is not None
    ]


def sec_filings_task(engine: Engine, cik: str, symbol: str, day: str) -> ScheduledTask:
    provider_id = _provider_id(engine, code="sec", name="SEC EDGAR")
    return ScheduledTask(
        task_name="sync_us_sec_filings",
        job_type="sec_filings",
        # 幂等标识：company + 日期（同一天重复触发只留一条活跃任务）
        dedupe_key=_sec_key(provider_id, cik, day),
        scope={
            "cik": cik,
            "display_symbol": symbol,
            "observation_start": day,
            "observation_end": day,
        },
        provider_id=provider_id,
        failure_policy="遵守 SEC 公平访问政策：串行限速，带 User-Agent",
        max_attempts=2,
    )


def build_cn_statement_tasks(
    engine: Engine, settings: Settings, now: datetime
) -> list[ScheduledTask]:
    day = now.date().isoformat()
    return [
        cn_statements_task(engine, company.display_symbol, day)
        for company in UniverseRepository(engine).tracked_cn_stocks()
    ]


def cn_statements_task(engine: Engine, symbol: str, day: str) -> ScheduledTask:
    provider_id = SyncJobRepository(engine).ensure_provider(
        code="eastmoney_f10",
        name="A股财报（东方财富 F10）",
        enabled=True,
        config_public={"upstream": "https://datacenter.eastmoney.com", "credential": "none"},
    )
    return ScheduledTask(
        task_name="sync_cn_statements",
        job_type="cn_statements",
        dedupe_key=f"cn_statements:{symbol}:{day}",
        scope={"display_symbol": symbol, "observation_start": day, "observation_end": day},
        provider_id=provider_id,
        failure_policy="最多重试 2 次，失败保留上次入库的值",
        max_attempts=2,
    )


def build_us_bar_tasks(engine: Engine, settings: Settings, now: datetime) -> list[ScheduledTask]:
    """美股日线：按最久未更新轮流，每轮限量，给 Tiingo 免费档（每小时 50 次）留余量。"""

    repository = UniverseRepository(engine)
    today = now.date()
    tasks: list[ScheduledTask] = []
    for company in repository.tracked_us_by_oldest_bar(settings.scheduler_us_bars_max_symbols):
        window = bars_window(repository.last_bar_date(company.instrument_id), today)
        if window is None:
            continue
        tasks.append(us_bars_task(engine, settings, company.display_symbol, window[0], window[1]))
    return tasks


def us_bars_task(
    engine: Engine, settings: Settings, symbol: str, start: date, end: date
) -> ScheduledTask:
    configured = bool(settings.tiingo_api_key.strip())
    provider_id = SyncJobRepository(engine).ensure_provider(
        code="tiingo",
        name="美股日线（Tiingo EOD）",
        enabled=configured,
        config_public={"upstream": "https://api.tiingo.com", "credential": "api_key"},
    )
    return ScheduledTask(
        task_name="sync_us_bars",
        job_type="tiingo_daily",
        dedupe_key=f"tiingo:{symbol}:{start.isoformat()}:{end.isoformat()}",
        scope={
            "display_symbol": symbol,
            "observation_start": start.isoformat(),
            "observation_end": end.isoformat(),
        },
        provider_id=provider_id,
        failure_policy="限额用尽时留在队列重试，不写空值",
        max_attempts=2,
        unavailable_reason=None if configured else "未配置 TIINGO_API_KEY，美股日线不可用",
    )


def build_universe_tasks(engine: Engine, settings: Settings, now: datetime) -> list[ScheduledTask]:
    """全市场证券清单：美股（SEC）与 A 股（巨潮）各一条，每周一次。"""

    day = now.date().isoformat()
    provider_id = SyncJobRepository(engine).ensure_provider(
        code="universe",
        name="证券清单（SEC / 巨潮）",
        enabled=True,
        config_public={"upstreams": ["https://www.sec.gov", "https://www.cninfo.com.cn"]},
    )
    return [
        ScheduledTask(
            task_name="sync_universe",
            job_type=job_type,
            dedupe_key=f"universe:{market}:{day}",
            scope={"display_symbol": f"universe-{market}", "observation_end": day},
            provider_id=provider_id,
            failure_policy="每周一次；失败保留上次清单",
            max_attempts=2,
        )
        for market, job_type in (("us", "us_universe"), ("cn", "cn_universe"))
    ]


def build_china_macro_tasks(
    engine: Engine, settings: Settings, now: datetime
) -> list[ScheduledTask]:
    """中国宏观六序列：一次登记，由执行器分源抓取；按天幂等。"""

    day = now.date().isoformat()
    provider_id = SyncJobRepository(engine).ensure_provider(
        code="china_macro",
        name="中国宏观（东方财富 / 商务部公开接口）",
        enabled=True,
        config_public={"transport": "public_web", "credential": "not_required"},
    )
    return [
        ScheduledTask(
            task_name="sync_china_macro",
            job_type="china_macro",
            dedupe_key=f"china_macro:{day}",
            scope={"display_symbol": "china-macro", "observation_end": day},
            provider_id=provider_id,
            failure_policy="分源抓取，部分失败保留已写入的数据",
            max_attempts=3,
        )
    ]


def build_board_market_tasks(
    engine: Engine, settings: Settings, now: datetime
) -> list[ScheduledTask]:
    """东财板块收盘快照与日线：一天一条，执行器自己判断是不是交易日。"""

    day = now.date().isoformat()
    provider_id = SyncJobRepository(engine).ensure_provider(
        code="eastmoney_boards",
        name="东财板块行情（公开网页接口）",
        enabled=True,
        config_public={"upstream": "https://quote.eastmoney.com", "credential": "none"},
    )
    return [
        ScheduledTask(
            task_name="sync_board_market",
            job_type="board_market",
            dedupe_key=f"board_market:{day}",
            scope={"display_symbol": "eastmoney-boards", "observation_end": day},
            provider_id=provider_id,
            failure_policy="非交易日不写快照；连续 3 次上游失败即停",
            max_attempts=3,
        )
    ]


def build_custom_analysis_tasks(
    engine: Engine, settings: Settings, now: datetime
) -> list[ScheduledTask]:
    """自定义数据分析：一天一条增量任务，执行器按已入库的日期只补缺口。"""

    day = now.date().isoformat()
    provider_id = SyncJobRepository(engine).ensure_provider(
        code="custom_analysis",
        name="自定义数据分析（交易所 / 中证指数 / 东方财富公开接口）",
        enabled=True,
        config_public={
            "upstream": "上交所、深交所、中金所、中证指数、东方财富数据中心",
            "credential": "none",
        },
    )
    return [
        ScheduledTask(
            task_name="sync_custom_analysis",
            job_type="custom_analysis",
            dedupe_key=f"custom_analysis:{day}",
            scope={"display_symbol": "custom-analysis", "observation_end": day},
            provider_id=provider_id,
            failure_policy="连续 3 次上游失败即停，已写入的保留",
            max_attempts=3,
        )
    ]


def build_financial_metrics_tasks(
    engine: Engine, settings: Settings, now: datetime
) -> list[ScheduledTask]:
    """被关注的美股 + A 股逐家生成财务体检快照；没有真实披露的公司，任务直接写 0 行，不造数。"""

    day = now.date().isoformat()
    universe = UniverseRepository(engine)
    targets = [("US", company) for company in universe.tracked_us()]
    targets += [("CN", company) for company in universe.tracked_cn_stocks()]
    provider_id = _derived_provider_id(engine)
    return [
        ScheduledTask(
            task_name="compute_financial_metrics",
            job_type="financial_metrics",
            # 幂等标识：instrument + 日期 + metric_version（同一天重复触发只会复用活跃任务）
            dedupe_key=f"metrics:{company.display_symbol}:{day}:{METRIC_VERSION}",
            scope={
                "instrument_id": str(company.instrument_id),
                "display_symbol": company.display_symbol,
                "market": market,
                "as_of": day,
                "metric_version": METRIC_VERSION,
            },
            provider_id=provider_id,
            failure_policy="缺数据降级不生成，缺失项标 unavailable",
            max_attempts=1,
        )
        for market, company in targets
    ]


def build_macro_regime_tasks(
    engine: Engine, settings: Settings, now: datetime
) -> list[ScheduledTask]:
    """宏观状态判断：按 as_of 生成，不覆盖旧报告。"""

    as_of = now.date().isoformat()
    return [
        ScheduledTask(
            task_name="compute_macro_regime",
            job_type="macro_regime",
            # 幂等标识：as_of + model_version
            dedupe_key=f"macro_regime:{as_of}:{MACRO_REGIME_MODEL_VERSION}",
            scope={"as_of": as_of, "model_version": MACRO_REGIME_MODEL_VERSION},
            provider_id=_derived_provider_id(engine),
            failure_policy="按 as_of 生成新报告，不覆盖旧报告",
            max_attempts=1,
        )
    ]


def build_hotspot_tasks(engine: Engine, settings: Settings, now: datetime) -> list[ScheduledTask]:
    """热点事件：只登记已授权的来源（目前是巨潮 / SEC 官方披露）；来源为空就如实标不可用。"""

    provider_id = SyncJobRepository(engine).ensure_provider(
        code="hotspot",
        name="热点来源（官方披露）",
        enabled=True,
        config_public={"sources": "cninfo, sec", "credential": "none"},
    )
    source = settings.scheduler_hotspot_source.strip()
    if not source:
        return [
            ScheduledTask(
                task_name="refresh_hotspot_events",
                job_type="hotspot_events",
                dedupe_key="hotspot:no_source",
                scope={},
                provider_id=provider_id,
                failure_policy="无授权来源直接禁用，不造数据",
                unavailable_reason="未配置授权来源，热点没有数据",
            )
        ]
    day = now.date().isoformat()
    return [
        ScheduledTask(
            task_name="refresh_hotspot_events",
            job_type="hotspot_events",
            # 幂等标识：date + source
            dedupe_key=f"hotspot:{source}:{day}",
            scope={"source": source, "observed_on": day},
            provider_id=provider_id,
            failure_policy="来源失败保留已入库的披露，失败项写入任务信息",
            max_attempts=2,
        )
    ]


def build_parse_document_tasks(
    engine: Engine, settings: Settings, now: datetime
) -> list[ScheduledTask]:
    """兜底扫描：把 ``documents`` 里 parse_status='queued' 的文件补登记。

    主路径是上传后立刻调 :func:`parse_document_task` 入队；这里只兜住「落了盘但没进队列」
    （比如上传接口在入队前被打断）。**只读** documents，不抢锁、不改状态——领文件是 worker 的事。
    同一份文件重复触发不会产生第二条 queued 记录，幂等键是 sha256+parser_version。
    """

    version = default_parser_version()
    with _session(engine) as session:
        rows = session.execute(
            select(Document.id, Document.sha256)
            .where(Document.parse_status == "queued", Document.storage_path.is_not(None))
            .order_by(Document.uploaded_at)
        ).all()
    tasks: list[ScheduledTask] = []
    for document_id, sha256 in rows:
        task = _parse_document_task(
            engine, settings, now, document_id=document_id, sha256=str(sha256), version=version
        )
        tasks.append(task)
    return tasks


def parse_document_task(
    engine: Engine,
    settings: Settings,
    now: datetime,
    *,
    document_id: UUID,
    sha256: str,
    parser_version: str | None = None,
) -> ScheduledTask:
    """上传接口用：按 sha256+parser_version 入队一条 parse_document。"""

    return _parse_document_task(
        engine,
        settings,
        now,
        document_id=document_id,
        sha256=sha256,
        version=parser_version or default_parser_version(),
    )


def _parse_document_task(
    engine: Engine,
    settings: Settings,
    now: datetime,
    *,
    document_id: UUID,
    sha256: str,
    version: str,
) -> ScheduledTask:
    if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
        raise SchedulerConfigurationError("摘要格式不正确")
    return ScheduledTask(
        task_name="parse_document",
        job_type="parse_document",
        # 幂等标识：sha256 + parser_version（规格书 §11）
        dedupe_key=parse_document_identity(sha256, version),
        scope={"document_id": str(document_id), "sha256": sha256, "parser_version": version},
        provider_id=_provider_id(engine, code="upload", name="本地上传"),
        failure_policy="格式失败写状态，不无限重试",
        max_attempts=1,
    )


# --------------------------------------------------------------------------- 小工具


def _as_text_scope(scope: dict[str, object]) -> dict[str, str]:
    return {key: value if isinstance(value, str) else str(value) for key, value in scope.items()}


def _derived_provider_id(engine: Engine) -> UUID:
    """本地派生计算（指标快照、宏观状态）没有外部数据源，登记为一个固定的本地来源。"""

    return SyncJobRepository(engine).ensure_provider(
        code="derived_local",
        name="本地派生计算",
        enabled=True,
        config_public={"transport": "local", "credential": "not_required"},
    )


def _provider_id(engine: Engine, *, code: str, name: str) -> UUID:
    """取数据源主键；缺失时登记一条停用记录并告警，不伪造健康状态。"""

    with _session(engine) as session:
        found = session.scalars(select(DataProvider).where(DataProvider.code == code)).one_or_none()
    if found is not None:
        return found.id
    logger.warning("scheduler provider missing code=%s enabled=false", code)
    return SyncJobRepository(engine).provider_id(
        code=code, name=name, config_public={"origin": "scheduler"}
    )


@contextmanager
def _session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session


def _sec_key(provider_id: UUID, cik: str, day: str) -> str:
    """幂等标识：company + filing accession；accession 由 worker 抓到后回填到 scope。"""

    return f"sec:{provider_id}:{cik}:{day}"


def _latest_quarter(day: date) -> str:
    quarter = (day.month - 1) // 3 + 1
    return f"{day.year}Q{quarter}"


def ensure_trading_day(
    repository: InstrumentRepository,
    exchange: str,
    session_date: date,
    *,
    lookback_days: int = MAX_TRADING_DAY_LOOKBACK,
) -> date | None:
    """交易日判断：复用 trading_calendars 的现有数据，往回找到最近的交易日。

    日历里没有该日期的记录时返回 None（unknown），由调用方按不可用处理，
    绝不猜「大概是交易日」。
    """

    cursor = session_date
    for _ in range(lookback_days + 1):
        opened = repository.calendar_open(exchange, cursor)
        if opened is None:
            return None
        if opened:
            return cursor
        cursor -= timedelta(days=1)
    return None


def build_scheduler(settings: Settings) -> BackgroundScheduler:
    """构造 APScheduler 后台调度器；调用方负责 start()/shutdown()。"""

    return BackgroundScheduler(
        timezone=UTC if settings.scheduler_timezone == "UTC" else None,
        job_defaults={"coalesce": True, "max_instances": 1},
    )


def describe_registry() -> list[dict[str, str]]:
    """自检用：把 §11 的表格与代码里的幂等标识对照出来。"""

    return [
        {
            "task": "sync_fred_series",
            "idempotency": "provider+series+date",
            "dedupe_key": "fred:{series_code}:{date}",
        },
        {
            "task": "sync_a_stock_bars",
            "idempotency": "provider+instrument+trade_date+adjustment",
            "dedupe_key": "akshare:{display_symbol}:{adjustment}:{trade_date}",
        },
        {
            "task": "sync_us_sec_filings",
            "idempotency": "company+filing accession",
            "dedupe_key": "sec:{provider}:{cik}:{observed_on}",
        },
        {
            "task": "parse_document",
            "idempotency": "sha256+parser_version",
            "dedupe_key": "parse_document:{sha256}:{parser_version}",
        },
        {
            "task": "compute_financial_metrics",
            "idempotency": "instrument+period+metric_version",
            "dedupe_key": f"metrics:{{symbol}}:{{period}}:{METRIC_VERSION}",
        },
        {
            "task": "compute_macro_regime",
            "idempotency": "as_of+model_version",
            "dedupe_key": f"macro_regime:{{as_of}}:{MACRO_REGIME_MODEL_VERSION}",
        },
        {
            "task": "refresh_hotspot_events",
            "idempotency": "date+source",
            "dedupe_key": "hotspot:{source}:{date}",
        },
    ]


AKSHARE_SYMBOLS = tuple(target.display_symbol for target in DAILY_TARGETS)
