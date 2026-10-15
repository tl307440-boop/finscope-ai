import asyncio
from datetime import date
from decimal import Decimal

import pytest
from app.providers.errors import ProviderError
from app.providers.http_client import OutboundRequest, RawResponse, ResilientHttpClient, Transport
from app.providers.sec_edgar import (
    companyfacts_url,
    fetch_companyfacts,
    load_fixture,
    parse_companyfacts,
    select_visible,
)


class ScriptTransport(Transport):
    def __init__(self) -> None:
        self.urls: list[str] = []
        self.headers: list[dict[str, str]] = []

    async def send(self, request: OutboundRequest) -> RawResponse:
        self.urls.append(request.url)
        self.headers.append(dict(request.headers))
        return RawResponse(status_code=200, headers={}, content=b"{}")


def test_fixture_keeps_units_apart_and_hides_later_amendments() -> None:
    facts = parse_companyfacts(load_fixture())
    assert {fact.unit for fact in facts} == {"USD", "shares"}
    early = select_visible(facts, date(2024, 3, 1))
    usd = [fact for fact in early if fact.unit == "USD" and fact.period_end == date(2023, 12, 31)]
    assert len(usd) == 1
    assert usd[0].value == Decimal("100")
    assert usd[0].accession_no == "0000320193-24-000001"
    assert usd[0].form == "10-K"
    assert usd[0].fiscal_year == 2023
    assert usd[0].fiscal_period == "FY"
    later = select_visible(facts, date(2024, 6, 1))
    amended = [
        fact for fact in later if fact.unit == "USD" and fact.period_end == date(2023, 12, 31)
    ]
    assert amended[0].value == Decimal("120")
    assert amended[0].form == "10-K/A"
    assert select_visible(facts, date(2023, 1, 31)) == []


def test_bad_payload_does_not_echo_the_body() -> None:
    with pytest.raises(ProviderError) as caught:
        parse_companyfacts(b"super-secret-body")
    assert "super-secret-body" not in caught.value.message


def test_missing_user_agent_does_not_call_sec() -> None:
    transport = ScriptTransport()
    client = ResilientHttpClient(transport, sleeper=_sleep, max_attempts=1)

    async def run() -> None:
        await fetch_companyfacts(client, "320193", "  ")

    with pytest.raises(ProviderError) as caught:
        asyncio.run(run())
    assert caught.value.code == "DATA_UNAVAILABLE"
    assert transport.urls == []


def test_requests_are_paced_and_identify_the_client() -> None:
    transport = ScriptTransport()
    delays: list[float] = []

    async def sleeper(seconds: float) -> None:
        delays.append(seconds)

    client = ResilientHttpClient(
        transport, sleeper=sleeper, min_interval_seconds=0.11, max_attempts=1
    )

    async def run() -> None:
        await fetch_companyfacts(client, "320193", "FinScopeAI research-admin@example.com")
        await fetch_companyfacts(client, "320193", "FinScopeAI research-admin@example.com")

    asyncio.run(run())
    assert transport.urls == [companyfacts_url("320193"), companyfacts_url("320193")]
    assert transport.headers[0]["User-Agent"] == "FinScopeAI research-admin@example.com"
    assert delays == [0.11]


async def _sleep(_: float) -> None:
    return None
