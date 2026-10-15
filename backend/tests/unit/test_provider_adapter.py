import asyncio
from datetime import date
from decimal import Decimal
from uuid import uuid4

from app.providers.errors import ProviderError, ProviderTimeout
from app.providers.fixtures import FIXTURE_NOTICE, fixture_bars
from app.providers.http_client import RawResponse, ResilientHttpClient, Transport
from app.providers.quality import RawBar, accept_bar, classify_bar
from app.services.ingest import ingest_bars
from app.services.provider_probe import probe_upstream


class ScriptedTransport(Transport):
    def __init__(self, steps: list[RawResponse | str]) -> None:
        self.steps = list(steps)
        self.calls = 0

    async def send(self, request: object) -> RawResponse:
        del request
        self.calls += 1
        step = self.steps.pop(0)
        if step == "timeout":
            raise ProviderTimeout()
        assert isinstance(step, RawResponse)
        return step


class MemoryWriter:
    def __init__(self) -> None:
        self.batches: list[list[object]] = []

    def upsert(self, bars: list[object], *, instrument_id: object, provider_id: object) -> int:
        del instrument_id, provider_id
        self.batches.append(list(bars))
        return len(bars)


def _run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


async def _no_sleep(_seconds: float) -> None:
    return None


def _bar(**overrides: object) -> RawBar:
    payload: dict[str, object] = {
        "trade_date": date(2024, 1, 2),
        "open": Decimal("10"),
        "high": Decimal("11"),
        "low": Decimal("9"),
        "close": Decimal("10.5"),
        "volume": Decimal("100"),
        "amount": Decimal("1000"),
        "adjustment": "none",
        "currency": "CNY",
        "unit": "price",
        "source_record_key": "unit-1",
    }
    payload.update(overrides)
    return RawBar.model_validate(payload)


def test_retries_429_then_returns_body_without_leaking_payload() -> None:
    transport = ScriptedTransport(
        [
            RawResponse(429, {"Retry-After": "0"}, b"secret-body"),
            RawResponse(200, {}, b"ok"),
        ]
    )
    sleeps: list[float] = []

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    async def scenario() -> bytes:
        client = ResilientHttpClient(transport, sleeper=sleeper, max_attempts=3)
        return await client.get("https://upstream.invalid/bars?api_key=super-secret")

    assert _run(scenario()) == b"ok"
    assert transport.calls == 2
    assert sleeps == [0]
    assert "super-secret" not in str(sleeps)


def test_exhausted_503_is_explicit_and_stops() -> None:
    transport = ScriptedTransport([RawResponse(503, {}, b"")] * 3)

    async def scenario() -> ProviderError:
        client = ResilientHttpClient(transport, sleeper=_no_sleep, max_attempts=3)
        try:
            await client.get("https://upstream.invalid/bars")
        except ProviderError as exc:
            return exc
        raise AssertionError("should fail")

    error = _run(scenario())
    assert isinstance(error, ProviderError)
    assert error.code == "UPSTREAM_UNAVAILABLE"
    assert error.attempts == 3
    assert transport.calls == 3


def test_timeout_is_retried_then_reported() -> None:
    transport = ScriptedTransport(["timeout", "timeout", "timeout"])

    async def scenario() -> ProviderError:
        client = ResilientHttpClient(transport, sleeper=_no_sleep, max_attempts=3)
        try:
            await client.get("https://upstream.invalid/bars")
        except ProviderError as exc:
            return exc
        raise AssertionError("should fail")

    error = _run(scenario())
    assert isinstance(error, ProviderError)
    assert error.code == "UPSTREAM_TIMEOUT"
    assert error.status_code == 504
    assert error.attempts == 3


def test_client_error_is_not_retried() -> None:
    transport = ScriptedTransport([RawResponse(400, {}, b"secret-body")])

    async def scenario() -> ProviderError:
        client = ResilientHttpClient(transport, sleeper=_no_sleep, max_attempts=3)
        try:
            await client.get("https://upstream.invalid/bars")
        except ProviderError as exc:
            return exc
        raise AssertionError("should fail")

    error = _run(scenario())
    assert isinstance(error, ProviderError)
    assert error.code == "UPSTREAM_ERROR"
    assert error.attempts == 1
    assert transport.calls == 1
    assert "secret-body" not in error.message


def test_retry_after_is_capped() -> None:
    transport = ScriptedTransport(
        [RawResponse(429, {"Retry-After": "99"}, b""), RawResponse(200, {}, b"ok")]
    )
    sleeps: list[float] = []

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    async def scenario() -> None:
        client = ResilientHttpClient(
            transport,
            sleeper=sleeper,
            max_attempts=2,
            backoff_cap_seconds=2,
        )
        await client.get("https://upstream.invalid/bars")

    _run(scenario())
    assert sleeps == [2]


def test_rate_limit_waits_between_calls() -> None:
    transport = ScriptedTransport([RawResponse(200, {}, b"a"), RawResponse(200, {}, b"b")])
    sleeps: list[float] = []

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    async def scenario() -> None:
        client = ResilientHttpClient(transport, sleeper=sleeper, min_interval_seconds=0.25)
        await client.get("https://upstream.invalid/a")
        await client.get("https://upstream.invalid/b")

    _run(scenario())
    assert sleeps == [0.25]


def test_ohlc_and_negative_volume_are_rejected() -> None:
    assert classify_bar(_bar()) == "ok"
    assert classify_bar(_bar(high=Decimal("10"))) == "rejected"
    assert classify_bar(_bar(volume=Decimal("-1"))) == "rejected"
    assert accept_bar(_bar(low=Decimal("12"))) is None


def test_adjusted_outlier_is_quarantined_not_rejected() -> None:
    checked = accept_bar(_bar(adjustment="qfq", high=Decimal("80"), close=Decimal("10.5")))
    assert checked is not None
    assert checked.quality_flag == "review"
    assert checked.quality_version == "bar-quality-v1"


def test_provider_failure_does_not_call_writer() -> None:
    writer = MemoryWriter()
    outcome = ingest_bars(
        ProviderError(code="UPSTREAM_UNAVAILABLE", message="上游暂时不可用", status_code=503),
        writer,
        instrument_id=uuid4(),
        provider_id=uuid4(),
    )
    assert outcome.rows_written == 0
    assert outcome.preserved_existing is True
    assert writer.batches == []


def test_rejected_bars_are_not_written() -> None:
    writer = MemoryWriter()
    outcome = ingest_bars(
        [_bar(), _bar(source_record_key="bad", high=Decimal("1"))],
        writer,
        instrument_id=uuid4(),
        provider_id=uuid4(),
    )
    assert outcome.rows_written == 1
    assert outcome.rows_rejected == 1
    assert len(writer.batches[0]) == 1


def test_live_mode_refuses_fixture_prices() -> None:
    try:
        fixture_bars("live")
    except ProviderError as exc:
        assert exc.code == "DEMO_NOT_ALLOWED"
    else:
        raise AssertionError("live mode returned fixture bars")
    bars = fixture_bars("demo")
    assert bars[0].source_record_key == "fixture:demo-bar"
    assert "合成样例" in FIXTURE_NOTICE


def test_probe_scenarios_fail_clearly() -> None:
    async def scenario(name: str) -> ProviderError:
        return await probe_upstream(name, max_attempts=2)

    limited = _run(scenario("status_429"))
    unavailable = _run(scenario("status_503"))
    timed_out = _run(scenario("timeout"))
    assert isinstance(limited, ProviderError)
    assert isinstance(unavailable, ProviderError)
    assert isinstance(timed_out, ProviderError)
    assert limited.code == "RATE_LIMITED"
    assert unavailable.code == "UPSTREAM_UNAVAILABLE"
    assert timed_out.code == "UPSTREAM_TIMEOUT"
    assert limited.attempts == 2
