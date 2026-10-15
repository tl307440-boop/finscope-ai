import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import cast
from urllib.parse import urlencode

from app.catalog.macro_series import catalog_codes
from app.providers.errors import ProviderError
from app.providers.http_client import ResilientHttpClient

FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
_ALL_VINTAGES_START = "1776-07-04"
_ALL_VINTAGES_END = "9999-12-31"


@dataclass(frozen=True)
class FredVintage:
    observation_date: date
    value: Decimal
    realtime_start: date
    realtime_end: date


def observations_url(
    *,
    series_id: str,
    api_key: str,
    observation_start: date,
    observation_end: date,
) -> str:
    query = urlencode(
        {
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "realtime_start": _ALL_VINTAGES_START,
            "realtime_end": _ALL_VINTAGES_END,
            "observation_start": observation_start.isoformat(),
            "observation_end": observation_end.isoformat(),
        }
    )
    return f"{FRED_OBSERVATIONS_URL}?{query}"


def without_secret(error: ProviderError, secret: str) -> ProviderError:
    if secret and secret in error.message:
        return ProviderError(
            code=error.code,
            message="上游请求失败",
            status_code=error.status_code,
            attempts=error.attempts,
        )
    return error


def parse_observations(payload: bytes) -> tuple[FredVintage, ...]:
    try:
        decoded: object = json.loads(payload, parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _payload_error() from exc
    if not isinstance(decoded, dict):
        raise _payload_error()
    document = cast(dict[str, object], decoded)
    raw_rows = document.get("observations")
    if not isinstance(raw_rows, list):
        raise _payload_error()
    parsed: list[FredVintage] = []
    for raw in raw_rows:
        item = _one_observation(raw)
        if item is not None:
            parsed.append(item)
    return tuple(parsed)


class FredAdapter:
    def __init__(self, client: ResilientHttpClient, api_key: str) -> None:
        self.client = client
        self.api_key = api_key

    async def fetch_vintages(
        self,
        series_id: str,
        observation_start: date,
        observation_end: date,
    ) -> tuple[FredVintage, ...]:
        if not self.api_key.strip():
            raise ProviderError(
                code="DATA_UNAVAILABLE",
                message="未配置 FRED 密钥，数据源不可用",
                status_code=503,
            )
        if series_id not in catalog_codes():
            raise ProviderError(
                code="VALIDATION_ERROR",
                message="不在已核对的宏观序列里",
                status_code=422,
            )
        url = observations_url(
            series_id=series_id,
            api_key=self.api_key,
            observation_start=observation_start,
            observation_end=observation_end,
        )
        try:
            body = await self.client.get(url)
        except ProviderError as error:
            raise without_secret(error, self.api_key) from error
        try:
            return parse_observations(body)
        except ProviderError as error:
            raise without_secret(error, self.api_key) from error


def assign_revisions(vintages: Sequence[FredVintage]) -> list[tuple[FredVintage, int]]:
    grouped: dict[date, list[FredVintage]] = {}
    for vintage in vintages:
        grouped.setdefault(vintage.observation_date, []).append(vintage)
    ranked: list[tuple[FredVintage, int]] = []
    for observed in sorted(grouped):
        ordered = sorted(grouped[observed], key=lambda item: item.realtime_start)
        for index, vintage in enumerate(ordered, start=1):
            ranked.append((vintage, index))
    return ranked


def _payload_error() -> ProviderError:
    return ProviderError(
        code="UPSTREAM_PAYLOAD",
        message="无法解析 FRED 响应",
        status_code=502,
    )


def _one_observation(raw: object) -> FredVintage | None:
    if not isinstance(raw, dict):
        raise _payload_error()
    row = cast(dict[str, object], raw)
    value = row.get("value")
    if value == ".":
        return None
    if isinstance(value, bool) or not isinstance(value, str | int | Decimal):
        raise _payload_error()
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except InvalidOperation as exc:
        raise _payload_error() from exc
    if not number.is_finite():
        raise _payload_error()
    return FredVintage(
        observation_date=_date_field(row.get("date")),
        value=number,
        realtime_start=_date_field(row.get("realtime_start")),
        realtime_end=_date_field(row.get("realtime_end")),
    )


def _date_field(value: object) -> date:
    if not isinstance(value, str):
        raise _payload_error()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise _payload_error() from exc
