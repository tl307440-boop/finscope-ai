import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import cast
from urllib.parse import urlencode

from app.catalog.macro_series import fred_codes
from app.providers.errors import ProviderError
from app.providers.http_client import ResilientHttpClient
from app.utils.time import utc_now

FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
# FRED 的 JSON 接口拒绝「版本日超过 2000 个」的实时区间。版本日按自然日计，
# 与观察日无关。终点用 9999-12-31 时，还没到来的日子没有版本，不计入这 2000。
# 政策利率每个自然日都有版本，从观察日起点一直取到今天会超过上限，所以按版本日切段。
_MAX_VINTAGE_DATES = 2000
_ALL_VINTAGES_END = date(9999, 12, 31)


@dataclass(frozen=True)
class FredVintage:
    observation_date: date
    value: Decimal
    realtime_start: date
    realtime_end: date


def vintage_windows(start: date, horizon: date) -> tuple[tuple[date, date], ...]:
    """把版本日区间切成每段至多 2000 天。最后一段的终点保持 9999-12-31。"""
    if horizon < start:
        horizon = start
    if (horizon - start).days + 1 <= _MAX_VINTAGE_DATES:
        return ((start, _ALL_VINTAGES_END),)
    windows: list[tuple[date, date]] = []
    cursor = start
    last_start = horizon - timedelta(days=_MAX_VINTAGE_DATES - 1)
    while cursor < last_start:
        chunk_end = cursor + timedelta(days=_MAX_VINTAGE_DATES - 1)
        windows.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    windows.append((cursor, _ALL_VINTAGES_END))
    return tuple(windows)


def merge_adjacent_vintages(vintages: Sequence[FredVintage]) -> tuple[FredVintage, ...]:
    """合并按版本日切段后被截断的同一版本。

    FRED 会把返回行的实时区间截到请求区间内，一个值若跨过切段边界就会变成首尾相接的两行。
    同一观察日、值相同、前一行终点的次日恰好是后一行起点时，它们是同一版本，合成一行；
    值不同的相邻行是真实修订，原样保留。
    """

    ordered = sorted(vintages, key=lambda item: (item.observation_date, item.realtime_start))
    merged: list[FredVintage] = []
    for item in ordered:
        last = merged[-1] if merged else None
        if (
            last is not None
            and last.observation_date == item.observation_date
            and last.value == item.value
            and last.realtime_end < _ALL_VINTAGES_END
            and last.realtime_end + timedelta(days=1) == item.realtime_start
        ):
            merged[-1] = FredVintage(
                observation_date=last.observation_date,
                value=last.value,
                realtime_start=last.realtime_start,
                realtime_end=item.realtime_end,
            )
            continue
        merged.append(item)
    return tuple(merged)


def observations_url(
    *,
    series_id: str,
    api_key: str,
    observation_start: date,
    observation_end: date,
    realtime_start: date,
    realtime_end: date,
) -> str:
    query = urlencode(
        {
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "realtime_start": realtime_start.isoformat(),
            "realtime_end": realtime_end.isoformat(),
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
        *,
        as_of: date | None = None,
    ) -> tuple[FredVintage, ...]:
        if not self.api_key.strip():
            raise ProviderError(
                code="DATA_UNAVAILABLE",
                message="未配置 FRED 密钥，数据源不可用",
                status_code=503,
            )
        if series_id not in fred_codes():
            raise ProviderError(
                code="VALIDATION_ERROR",
                message="不在已核对的宏观序列里",
                status_code=422,
            )
        horizon = max(observation_end, as_of or utc_now().date())
        collected: list[FredVintage] = []
        for realtime_start, realtime_end in vintage_windows(observation_start, horizon):
            url = observations_url(
                series_id=series_id,
                api_key=self.api_key,
                observation_start=observation_start,
                observation_end=observation_end,
                realtime_start=realtime_start,
                realtime_end=realtime_end,
            )
            try:
                body = await self.client.get(url)
            except ProviderError as error:
                raise without_secret(error, self.api_key) from error
            try:
                collected.extend(parse_observations(body))
            except ProviderError as error:
                raise without_secret(error, self.api_key) from error
        return merge_adjacent_vintages(collected)


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
