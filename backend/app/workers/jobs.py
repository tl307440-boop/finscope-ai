import asyncio
from datetime import date
from pathlib import Path
from uuid import UUID

from sqlalchemy import Engine

from app.providers.akshare_bars import UNSUPPORTED_SYMBOL_MESSAGE, AdjustmentCode, target_for
from app.providers.errors import ProviderError
from app.providers.fred import FredVintage
from app.repositories.board_market import BoardMarketRepository
from app.repositories.custom_analysis import CustomAnalysisRepository
from app.repositories.macro import MacroRepository
from app.repositories.sync_jobs import JobSnapshot
from app.services.akshare_sync import apply_bars, fetch_bars
from app.services.board_market_sync import sync_board_market
from app.services.custom_analysis_sync import sync_custom_analysis
from app.services.data_sync import (
    sync_china_macro,
    sync_cn_statements,
    sync_cn_universe,
    sync_sec_facts,
    sync_us_bars,
    sync_us_universe,
)
from app.services.document_index import index_document_by_id
from app.services.fred_sync import fetch_from_fred, sync_fred_series
from app.services.hotspot_sync import sync_hotspot_events
from app.services.macro_regime import compute_macro_regime
from app.services.market_cache import PacedGateway
from app.services.metrics_snapshot import snapshot_company_metrics
from app.services.runtime_settings import effective_settings
from app.utils.time import utc_now

# 与 app.api.v1.documents 的默认上传根目录保持一致（都在 backend 目录下起进程）。
UPLOAD_ROOT = Path("data/uploads")


def execute_job(engine: Engine, job: JobSnapshot) -> int:
    # 每个任务都读最新配置（带 5 秒缓存）：界面里填的密钥不用重启 worker 就生效。
    settings = effective_settings(engine)
    if job.job_type == "akshare_daily":
        return _akshare(engine, job.scope)
    if job.job_type == "fred_series":
        return _fred(engine, job.scope, settings.fred_api_key)
    if job.job_type == "parse_document":
        return _parse_document(engine, job.scope)
    if job.job_type == "sec_filings":
        return asyncio.run(
            sync_sec_facts(
                engine,
                cik=_text(job.scope, "cik"),
                user_agent=settings.sec_user_agent,
            )
        )
    if job.job_type == "cn_statements":
        return asyncio.run(
            sync_cn_statements(engine, display_symbol=_text(job.scope, "display_symbol"))
        )
    if job.job_type == "tiingo_daily":
        return asyncio.run(
            sync_us_bars(
                engine,
                display_symbol=_text(job.scope, "display_symbol"),
                start=_day(job.scope, "observation_start"),
                end=_day(job.scope, "observation_end"),
                token=settings.tiingo_api_key,
            )
        )
    if job.job_type == "us_universe":
        return asyncio.run(sync_us_universe(engine, user_agent=settings.sec_user_agent))
    if job.job_type == "cn_universe":
        return asyncio.run(sync_cn_universe(engine))
    if job.job_type == "china_macro":
        return asyncio.run(sync_china_macro(engine))
    if job.job_type == "board_market":
        return asyncio.run(
            sync_board_market(BoardMarketRepository(engine), PacedGateway(), utc_now())
        )
    if job.job_type == "custom_analysis":
        return _custom_analysis(engine)
    if job.job_type == "financial_metrics":
        return snapshot_company_metrics(
            engine, _text(job.scope, "instrument_id"), _day(job.scope, "as_of")
        )
    if job.job_type == "macro_regime":
        return compute_macro_regime(
            engine, as_of=_day(job.scope, "as_of"), model_version=_text(job.scope, "model_version")
        )
    if job.job_type == "hotspot_events":
        return asyncio.run(
            sync_hotspot_events(
                engine,
                source=_text(job.scope, "source"),
                observed_on=_day(job.scope, "observed_on"),
                user_agent=settings.sec_user_agent,
            )
        )
    raise ProviderError(code="VALIDATION_ERROR", message="不认识的任务类型", status_code=422)


def _custom_analysis(engine: Engine) -> int:
    report = asyncio.run(
        sync_custom_analysis(CustomAnalysisRepository(engine), PacedGateway(), utc_now())
    )
    if report.failures:
        raise ProviderError(
            code="PARTIAL_SYNC",
            message=f"{'；'.join(report.failures)[:300]}（已写入 {report.total} 行）",
            status_code=503,
        )
    return report.total


def _akshare(engine: Engine, scope: dict[str, object]) -> int:
    symbol = _text(scope, "display_symbol")
    adjustment = _adjustment(scope.get("adjustment"))
    start = _day(scope, "observation_start")
    end = _day(scope, "observation_end")
    if target_for(symbol) is None:
        raise ProviderError(
            code="VALIDATION_ERROR",
            message=UNSUPPORTED_SYMBOL_MESSAGE,
            status_code=422,
        )
    try:
        payload = asyncio.run(fetch_bars(symbol, start, end, adjustment))
    except ProviderError as exc:
        if exc.code == "NO_ROWS":
            return 0
        raise
    outcome = apply_bars(
        engine,
        payload,
        display_symbol=symbol,
        adjustment=adjustment,
    )
    return outcome.rows_written


def _fred(engine: Engine, scope: dict[str, object], api_key: str) -> int:
    series_code = _text(scope, "series_code")
    start = _day(scope, "observation_start")
    end = _day(scope, "observation_end")
    repository = MacroRepository(engine)

    async def fetch(
        code: str,
        observation_start: date,
        observation_end: date,
    ) -> tuple[FredVintage, ...]:
        return await fetch_from_fred(
            api_key,
            code,
            observation_start,
            observation_end,
        )

    return asyncio.run(
        sync_fred_series(
            repository,
            series_code=series_code,
            observation_start=start,
            observation_end=end,
            fetch=fetch,
            fetched_at=utc_now(),
        )
    )


def _parse_document(engine: Engine, scope: dict[str, object]) -> int:
    """parse_document：按 scope 里的 document_id 解析并建索引。

    解析器名/版本写进最终写回的 message，便于和幂等键 sha256+parser_version 对上。
    """

    raw_id = _text(scope, "document_id")
    try:
        document_id = UUID(raw_id)
    except ValueError as exc:
        raise ProviderError(
            code="VALIDATION_ERROR", message="任务范围里的文档标识不正确", status_code=422
        ) from exc
    outcome = index_document_by_id(engine, UPLOAD_ROOT, document_id)
    if outcome is None:
        raise ProviderError(code="NOT_FOUND", message="文档不存在", status_code=404)
    return outcome.chunk_count


def _text(scope: dict[str, object], key: str) -> str:
    value = scope.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProviderError(code="VALIDATION_ERROR", message="任务范围不完整", status_code=422)
    return value.strip()


def _day(scope: dict[str, object], key: str) -> date:
    raw = _text(scope, key)
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ProviderError(
            code="VALIDATION_ERROR", message="任务日期无法解析", status_code=422
        ) from exc


def _adjustment(value: object) -> AdjustmentCode:
    if value == "qfq":
        return "qfq"
    if value == "hfq":
        return "hfq"
    if value == "none" or value is None:
        return "none"
    raise ProviderError(code="VALIDATION_ERROR", message="不认识的复权方式", status_code=422)
