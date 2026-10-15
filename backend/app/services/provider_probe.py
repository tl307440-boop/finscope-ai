from app.providers.errors import ProviderError, ProviderTimeout
from app.providers.http_client import OutboundRequest, RawResponse, ResilientHttpClient, Transport


class ScriptTransport(Transport):
    def __init__(self, scenario: str) -> None:
        self.scenario = scenario

    async def send(self, request: OutboundRequest) -> RawResponse:
        del request
        if self.scenario == "timeout":
            raise ProviderTimeout()
        if self.scenario == "status_429":
            return RawResponse(status_code=429, headers={"Retry-After": "0"}, content=b"")
        if self.scenario == "status_503":
            return RawResponse(status_code=503, headers={}, content=b"")
        return RawResponse(status_code=200, headers={}, content=b"{}")


async def _no_sleep(_seconds: float) -> None:
    return None


async def probe_upstream(scenario: str, *, max_attempts: int = 3) -> ProviderError:
    client = ResilientHttpClient(
        ScriptTransport(scenario),
        sleeper=_no_sleep,
        max_attempts=max_attempts,
        min_interval_seconds=0,
    )
    try:
        await client.get("https://upstream.invalid/probe")
    except ProviderError as exc:
        return exc
    return ProviderError(code="UPSTREAM_ERROR", message="模拟探测未按预期失败", status_code=502)


def probe_details(error: ProviderError) -> dict[str, object]:
    return {
        "rows_written": 0,
        "attempts": error.attempts,
        "preserved_existing": True,
    }
