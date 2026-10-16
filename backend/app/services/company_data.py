"""单个公司的数据就绪度，以及「首次搜索时按需抓取」。

每家公司有两个数据集：财报（财务区与研究报告的来源）与日线（行情区的来源）。
对每个数据集给出：新鲜度、是否正在后台同步、数据日期与最近同步时间。

按需抓取的原则：
* 只在数据缺失或过期时才登记任务，且复用调度器同一套幂等键，不会和定时任务重复；
* 任务走同一个队列与 worker，所以同样遵守 SEC 的限速与 Tiingo 的免费额度；
* 上游明确说没有这个代码（404）就标 unavailable 并长时间不再重试；
* 其它失败有冷却期，防止页面刷新把上游打爆；队列积压过多时直接拒绝新的按需请求。
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import Engine

from app.core.config import Settings
from app.core.exceptions import AppError
from app.repositories.cn_financials import CnFinancialRepository
from app.repositories.filings import FilingRepository
from app.repositories.instruments import InstrumentRecord
from app.repositories.sync_jobs import JobSnapshot, SyncJobRepository
from app.repositories.universe import UniverseRepository
from app.services import freshness
from app.services.data_sync import bars_window
from app.services.scheduler import (
    ScheduledTask,
    cn_bars_task,
    cn_statements_task,
    sec_filings_task,
    us_bars_task,
)
from app.utils.identifiers import normalize_cik

State = Literal["fresh", "warn", "stale", "missing", "unavailable"]
SyncState = Literal["idle", "queued", "running", "failed"]

ONDEMAND_PRIORITY = 10
# 上游明确回「没有这个代码」后，这么久之内不再重试。
NOT_FOUND_COOLDOWN = timedelta(hours=24)
MAX_ACTIVE_JOBS = 200


@dataclass(frozen=True)
class DatasetStatus:
    dataset: str
    label: str
    source: str
    state: State
    sync: SyncState
    latest_data_date: date | None
    last_synced_at: datetime | None
    message: str
    can_request: bool


@dataclass(frozen=True)
class _Spec:
    dataset: str
    label: str
    source: str
    job_type: str
    threshold: freshness.Threshold
    by_data_date: bool
    available: bool
    unavailable_reason: str
    latest_data: date | None
    last_synced: datetime | None


@dataclass(frozen=True)
class Evaluation:
    state: State
    sync: SyncState
    message: str
    enqueue: bool


def evaluate(
    *,
    spec: _Spec,
    job: JobSnapshot | None,
    now: datetime,
    cooldown: timedelta,
) -> Evaluation:
    """纯判定：新鲜度 + 当前任务状态 → 对外状态与是否需要登记任务。"""

    if not spec.available:
        return Evaluation("unavailable", "idle", spec.unavailable_reason, False)
    if spec.by_data_date:
        base = freshness.day_state(spec.latest_data, now.date(), spec.threshold)
    else:
        base = freshness.sync_state(spec.last_synced, now, spec.threshold)
    state: State = base
    if job is not None and job.status in {"queued", "running"}:
        running: SyncState = "running" if job.status == "running" else "queued"
        return Evaluation(state, running, "后台正在抓取，完成后刷新页面即可", False)
    if job is not None and job.status == "failed" and job.finished_at is not None:
        wait = NOT_FOUND_COOLDOWN if job.error_code == "NOT_FOUND" else cooldown
        if now - job.finished_at < wait:
            reason = job.error_summary or "上次抓取失败"
            if job.error_code == "NOT_FOUND" and base == "missing":
                return Evaluation("unavailable", "failed", reason, False)
            return Evaluation(state, "failed", f"上次抓取失败：{reason}", False)
    needs = base in {"warn", "stale", "missing"}
    return Evaluation(state, "idle", "" if not needs else _stale_hint(base), needs)


def _stale_hint(state: freshness.Freshness) -> str:
    return {
        "missing": "还没有数据",
        "warn": "数据偏旧，可以刷新",
        "stale": "数据已过期，需要刷新",
        "fresh": "",
    }[state]


class CompanyDataService:
    def __init__(self, engine: Engine, settings: Settings) -> None:
        self.engine = engine
        self.settings = settings
        self.universe = UniverseRepository(engine)
        self.jobs = SyncJobRepository(engine)

    # ------------------------------------------------------------------ 状态

    def status(self, instrument: InstrumentRecord, now: datetime) -> list[DatasetStatus]:
        return [
            self._status(spec, now, instrument.display_symbol) for spec in self._specs(instrument)
        ]

    def _status(self, spec: _Spec, now: datetime, symbol: str) -> DatasetStatus:
        job = self.jobs.latest_for(spec.job_type, symbol)
        result = evaluate(spec=spec, job=job, now=now, cooldown=self._cooldown())
        return DatasetStatus(
            dataset=spec.dataset,
            label=spec.label,
            source=spec.source,
            state=result.state,
            sync=result.sync,
            latest_data_date=spec.latest_data,
            last_synced_at=spec.last_synced,
            message=result.message,
            can_request=result.enqueue,
        )

    # ------------------------------------------------------------------ 按需抓取

    def ensure(
        self, instrument: InstrumentRecord, now: datetime, requested_by: UUID
    ) -> list[DatasetStatus]:
        """缺失或过期的数据集登记后台任务，然后返回最新状态。"""

        specs = self._specs(instrument)
        wanted: list[tuple[_Spec, ScheduledTask]] = []
        for spec in specs:
            job = self.jobs.latest_for(spec.job_type, instrument.display_symbol)
            result = evaluate(spec=spec, job=job, now=now, cooldown=self._cooldown())
            if not result.enqueue:
                continue
            task = self._task(instrument, spec, now.date())
            if task is not None and task.unavailable_reason is None:
                wanted.append((spec, task))
        if wanted and self.jobs.count_active() >= MAX_ACTIVE_JOBS:
            raise AppError(
                code="QUEUE_BUSY",
                message="后台抓取队列繁忙，请稍后再试",
                status_code=429,
            )
        for _spec, task in wanted:
            self.jobs.enqueue(
                job_type=task.job_type,
                provider_id=task.provider_id,
                scope=dict(task.scope),
                dedupe_key=task.dedupe_key,
                requested_by=requested_by,
                now=now,
                max_attempts=task.max_attempts,
                priority=ONDEMAND_PRIORITY,
            )
        return [
            self._status(spec, now, instrument.display_symbol) for spec in self._specs(instrument)
        ]

    def _task(self, instrument: InstrumentRecord, spec: _Spec, today: date) -> ScheduledTask | None:
        symbol = instrument.display_symbol
        day = today.isoformat()
        if spec.job_type == "sec_filings" and instrument.cik:
            return sec_filings_task(self.engine, normalize_cik(instrument.cik), symbol, day)
        if spec.job_type == "cn_statements":
            return cn_statements_task(self.engine, symbol, day)
        window = bars_window(self.universe.last_bar_date(instrument.id), today)
        if window is None:
            return None
        if spec.job_type == "tiingo_daily":
            return us_bars_task(self.engine, self.settings, symbol, window[0], window[1])
        if spec.job_type == "akshare_daily":
            return cn_bars_task(self.engine, symbol, window[0], window[1])
        return None

    # ------------------------------------------------------------------ 数据集清单

    def _specs(self, instrument: InstrumentRecord) -> list[_Spec]:
        specs: list[_Spec] = []
        bars_latest = self.universe.last_bar_date(instrument.id)
        if instrument.market == "US":
            specs.append(self._us_financials(instrument))
            has_key = bool(self.settings.tiingo_api_key.strip())
            specs.append(
                _Spec(
                    dataset="bars",
                    label="日线行情",
                    source="Tiingo EOD",
                    job_type="tiingo_daily",
                    threshold=freshness.BARS,
                    by_data_date=True,
                    available=has_key or bars_latest is not None,
                    unavailable_reason="未配置美股行情源密钥（TIINGO_API_KEY），日线不可用",
                    latest_data=bars_latest,
                    last_synced=None,
                )
            )
            return specs
        if instrument.instrument_type == "STOCK":
            specs.append(self._cn_financials(instrument))
        specs.append(
            _Spec(
                dataset="bars",
                label="日线行情",
                source="东方财富 / 腾讯行情",
                job_type="akshare_daily",
                threshold=freshness.BARS,
                by_data_date=True,
                available=True,
                unavailable_reason="",
                latest_data=bars_latest,
                last_synced=None,
            )
        )
        return specs

    def _us_financials(self, instrument: InstrumentRecord) -> _Spec:
        cik = normalize_cik(instrument.cik) if instrument.cik else None
        filings = FilingRepository(self.engine)
        last = self.universe.last_success("sec_filings", instrument.display_symbol)
        if last is None and cik is not None:
            last = filings.last_edgar_recorded_at(cik)
        return _Spec(
            dataset="financials",
            label="财务报表",
            source="SEC EDGAR",
            job_type="sec_filings",
            threshold=freshness.FINANCIALS,
            by_data_date=False,
            available=cik is not None,
            unavailable_reason="没有 SEC 编号（CIK），无法抓取财报",
            latest_data=None if cik is None else filings.latest_edgar_period_end(cik),
            last_synced=last,
        )

    def _cn_financials(self, instrument: InstrumentRecord) -> _Spec:
        repository = CnFinancialRepository(self.engine)
        last = self.universe.last_success("cn_statements", instrument.display_symbol)
        if last is None:
            last = repository.last_fetched_at(instrument.id)
        return _Spec(
            dataset="financials",
            label="财务报表",
            source="东方财富 F10",
            job_type="cn_statements",
            threshold=freshness.FINANCIALS,
            by_data_date=False,
            available=True,
            unavailable_reason="",
            latest_data=repository.latest_period_end(instrument.id),
            last_synced=last,
        )

    def _cooldown(self) -> timedelta:
        return timedelta(minutes=self.settings.ondemand_retry_cooldown_minutes)
