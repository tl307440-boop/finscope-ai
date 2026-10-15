from app.providers.errors import ProviderError

FINAL_FAILURE_CODES = frozenset({"VALIDATION_ERROR", "NOT_FOUND", "DATA_UNAVAILABLE"})
LEASE_SECONDS = 300


def should_requeue(*, attempts: int, max_attempts: int, error_code: str) -> bool:
    if error_code in FINAL_FAILURE_CODES:
        return False
    return attempts < max_attempts


def public_failure(exc: BaseException) -> tuple[str, str]:
    """失败说明不能带上连接串或密钥。"""

    if isinstance(exc, ProviderError):
        message = exc.message
        if "://" in message or "api_key=" in message:
            return exc.code, "任务执行失败"
        return exc.code, message
    return "TASK_FAILED", "任务执行失败"


def dedupe_key_for(job_type: str, scope: dict[str, str]) -> str:
    if job_type == "akshare_daily":
        return "akshare:{display_symbol}:{adjustment}:{observation_start}:{observation_end}".format(
            **scope
        )
    if job_type == "fred_series":
        return "fred:{series_code}:{observation_start}:{observation_end}".format(**scope)
    raise ValueError("不认识的任务类型")
