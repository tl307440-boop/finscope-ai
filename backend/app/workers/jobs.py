import asyncio
from datetime import date

from sqlalchemy import Engine

from app.core.config import get_settings
from app.providers.akshare_bars import AdjustmentCode, target_for
from app.providers.errors import ProviderError
from app.providers.fred import FredVintage
from app.repositories.macro import MacroRepository
from app.repositories.sync_jobs import JobSnapshot
from app.services.akshare_sync import apply_kline_payload, fetch_klines
from app.services.fred_sync import fetch_from_fred, sync_fred_series
from app.utils.time import utc_now


def execute_job(engine: Engine, job: JobSnapshot) -> int:
    if job.job_type == "akshare_daily":
        return _akshare(engine, job.scope)
    if job.job_type == "fred_series":
        return _fred(engine, job.scope)
    raise ProviderError(code="VALIDATION_ERROR", message="不认识的任务类型", status_code=422)


def _akshare(engine: Engine, scope: dict[str, object]) -> int:
    symbol = _text(scope, "display_symbol")
    adjustment = _adjustment(scope.get("adjustment"))
    start = _day(scope, "observation_start")
    end = _day(scope, "observation_end")
    if target_for(symbol) is None:
        raise ProviderError(
            code="VALIDATION_ERROR",
            message="这三只股票以外的日线尚未开放",
            status_code=422,
        )
    payload = asyncio.run(fetch_klines(symbol, start, end, adjustment))
    outcome = apply_kline_payload(
        engine,
        payload,
        display_symbol=symbol,
        adjustment=adjustment,
    )
    return outcome.rows_written


def _fred(engine: Engine, scope: dict[str, object]) -> int:
    settings = get_settings()
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
            settings.fred_api_key,
            code,
            observation_start,
            observation_end,
        )

    return asyncio.run(
        sync_fred_series(
            repository,
            api_key=settings.fred_api_key,
            series_code=series_code,
            observation_start=start,
            observation_end=end,
            fetch=fetch,
            fetched_at=utc_now(),
        )
    )


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
