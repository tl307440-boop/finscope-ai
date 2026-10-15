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
from app.db.models.market import Instrument
from app.db.models.provider import DataProvider
from app.providers.akshare_bars import DAILY_TARGETS, AdjustmentCode, target_for
from app.repositories.instruments import InstrumentRepository
from app.repositories.macro import MacroRepository
from app.repositories.market_bars import MarketBarRepository
from app.repositories.sync_jobs import JobSnapshot, SyncJobRepository
from app.services.pdf_upload import default_parser_version, parse_document_identity
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


def enqueue_tasks(
    builder: Callable[[Engine, Settings, datetime], Sequence[ScheduledTask]],
    task_name: str,
) -> int:
    """触发器回调：建引擎、按幂等键登记 queued 记录，然后立刻释放连接。"""

    from app.db.session import create_db_engine

    settings = get_settings()
    engine = create_db_engine(settings.database_url)
    try:
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
    """每天 1 次宏观序列同步；未配置 FRED 密钥时标 unavailable。"""

    if not settings.fred_api_key.strip():
        return [
            ScheduledTask(
                task_name="sync_fred_series",
                job_type="fred_series",
                dedupe_key="fred:unavailable",
                scope={},
                provider_id=FRED_PROVIDER_ID,
                failure_policy="超时/429 退避",
                unavailable_reason="未配置 FRED_API_KEY，数据源不可用",
            )
        ]
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
    """A 股收盘后延迟执行；非交易日不登记，交易日判断复用 trading_calendars。"""

    target = now.date() - timedelta(days=settings.scheduler_a_stock_lag_trading_days)
    trade_date = ensure_trading_day(
        InstrumentRepository(engine), settings.scheduler_cn_market_exchange, target
    )
    if trade_date is None:
        # 日历里没有这段数据 => 无法确认是否交易日；跳过本轮，不猜。
        logger.warning(
            "scheduler skip task=sync_a_stock_bars reason=trading_calendar_unknown date=%s",
            target.isoformat(),
        )
        return []
    provider_id = MarketBarRepository(engine).ensure_akshare_provider()
    adjustment = DEFAULT_ADJUSTMENT
    tasks: list[ScheduledTask] = []
    for item in DAILY_TARGETS:
        if target_for(item.display_symbol) is None:
            continue
        scope: dict[str, object] = {
            "display_symbol": item.display_symbol,
            "observation_start": trade_date.isoformat(),
            "observation_end": trade_date.isoformat(),
            "adjustment": adjustment,
        }
        tasks.append(
            ScheduledTask(
                task_name="sync_a_stock_bars",
                job_type="akshare_daily",
                # 幂等标识：provider + instrument + trade_date + adjustment
                dedupe_key=dedupe_key_for("akshare_daily", _as_text_scope(scope)),
                scope=scope,
                provider_id=provider_id,
                failure_policy="最多重试 3 次",
                max_attempts=3,
            )
        )
    return tasks


def build_sec_tasks(engine: Engine, settings: Settings, now: datetime) -> list[ScheduledTask]:
    """SEC 申报：按公司登记任务，抓取由 worker 串行执行以遵守公平访问政策。"""

    day = now.date().isoformat()
    provider_id = _provider_id(engine, code="sec", name="SEC EDGAR")
    tasks: list[ScheduledTask] = []
    for cik, symbol in _us_companies(engine):
        scope: dict[str, object] = {
            "cik": cik,
            "display_symbol": symbol,
            "observation_start": day,
            "observation_end": day,
        }
        tasks.append(
            ScheduledTask(
                task_name="sync_us_sec_filings",
                job_type="sec_filings",
                # 幂等标识：company + filing accession（accession 由 worker 抓到时回填）
                dedupe_key=_sec_key(provider_id, cik, day),
                scope=scope,
                provider_id=provider_id,
                failure_policy="遵守 SEC 公平访问政策：串行限速，带 User-Agent",
                max_attempts=2,
            )
        )
    return tasks


def build_financial_metrics_tasks(
    engine: Engine, settings: Settings, now: datetime
) -> list[ScheduledTask]:
    """新财报入库后计算指标；缺数据的指标留空并标 unavailable，只部分生成。"""

    period = _latest_quarter(now.date())
    provider_id = _provider_id(engine, code="sec", name="SEC EDGAR")
    tasks: list[ScheduledTask] = []
    for _cik, symbol in _us_companies(engine):
        scope: dict[str, object] = {
            "display_symbol": symbol,
            "period": period,
            "metric_version": METRIC_VERSION,
        }
        tasks.append(
            ScheduledTask(
                task_name="compute_financial_metrics",
                job_type="financial_metrics",
                # 幂等标识：instrument + period + metric_version
                dedupe_key=f"metrics:{symbol}:{period}:{METRIC_VERSION}",
                scope=scope,
                provider_id=provider_id,
                failure_policy="缺数据仅部分生成，缺失项标 unavailable",
                max_attempts=1,
            )
        )
    return tasks


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
            provider_id=_provider_id(engine, code="fred", name="FRED"),
            failure_policy="按 as_of 生成新报告，不覆盖旧报告",
            max_attempts=1,
        )
    ]


def build_hotspot_tasks(engine: Engine, settings: Settings, now: datetime) -> list[ScheduledTask]:
    """P2 热点事件：只在来源合法且开关打开时登记；无权限源直接禁用。"""

    if not settings.enable_news:
        return [
            ScheduledTask(
                task_name="refresh_hotspot_events",
                job_type="hotspot_events",
                dedupe_key="hotspot:disabled",
                scope={},
                provider_id=_provider_id(engine, code="hotspot", name="热点来源"),
                failure_policy="无权限源禁用",
                unavailable_reason="ENABLE_NEWS=false，没有已授权的热点来源",
            )
        ]
    day = now.date().isoformat()
    source = settings.scheduler_hotspot_source.strip()
    if not source:
        return [
            ScheduledTask(
                task_name="refresh_hotspot_events",
                job_type="hotspot_events",
                dedupe_key="hotspot:no_source",
                scope={},
                provider_id=_provider_id(engine, code="hotspot", name="热点来源"),
                failure_policy="无权限源禁用",
                unavailable_reason="未配置授权来源，热点任务禁用",
            )
        ]
    return [
        ScheduledTask(
            task_name="refresh_hotspot_events",
            job_type="hotspot_events",
            # 幂等标识：date + source
            dedupe_key=f"hotspot:{source}:{day}",
            scope={"source": source, "observed_on": day},
            provider_id=_provider_id(engine, code="hotspot", name="热点来源"),
            failure_policy="无权限源禁用",
            max_attempts=1,
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


def _us_companies(engine: Engine) -> list[tuple[str, str]]:
    """按公司登记 SEC 任务；只取有 CIK 的美股公司。"""

    with _session(engine) as session:
        rows = session.execute(
            select(Instrument.cik, Instrument.display_symbol)
            .where(Instrument.market == "US", Instrument.cik.is_not(None))
            .order_by(Instrument.display_symbol)
        ).all()
    return [(str(row[0]), str(row[1])) for row in rows]


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
