"""FRED 公开图表 CSV（fred.stlouisfed.org/graph/fredgraph.csv），不需要 API 密钥。

和带密钥的 observations 接口不同，这里只有「抓取当下的最新修订」，没有历史版本，
也不返回发布日期。可见起点按抓取时刻推出来：FRED 的发布集中在纽约工作时段，
纽约 06:00 之前抓到的数都已在前一个纽约日公开，所以记为前一日；06:00 之后抓取
则记为当天，按 vintage_visible 的规则次日才可见，宁可晚一天也不提前。
"""

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from app.catalog.macro_series import fred_codes
from app.providers.errors import ProviderError
from app.providers.fred import FredVintage
from app.providers.http_client import ResilientHttpClient
from app.providers.vintage import NEW_YORK

FRED_GRAPH_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
OPEN_ENDED = date(9999, 12, 31)
_QUIET_HOURS_END = 6


def graph_csv_url(*, series_id: str, observation_start: date, observation_end: date) -> str:
    query = urlencode(
        {
            "id": series_id,
            "cosd": observation_start.isoformat(),
            "coed": observation_end.isoformat(),
        }
    )
    return f"{FRED_GRAPH_CSV_URL}?{query}"


def public_realtime_start(fetched_at: datetime) -> date:
    if fetched_at.tzinfo is None:
        raise ValueError("fetched_at 必须带时区")
    local = fetched_at.astimezone(NEW_YORK)
    if local.hour < _QUIET_HOURS_END:
        return local.date() - timedelta(days=1)
    return local.date()


def parse_graph_csv(
    payload: bytes, *, series_id: str, realtime_start: date
) -> tuple[FredVintage, ...]:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _payload_error() from exc
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise _payload_error()
    header = [cell.strip() for cell in lines[0].split(",")]
    if len(header) != 2 or header[1] != series_id:
        raise _payload_error()
    parsed: list[FredVintage] = []
    for line in lines[1:]:
        cells = line.split(",")
        if len(cells) != 2:
            raise _payload_error()
        raw_date, raw_value = cells[0].strip(), cells[1].strip()
        if raw_value in {"", "."}:
            continue
        try:
            observed = date.fromisoformat(raw_date)
            value = Decimal(raw_value)
        except (ValueError, InvalidOperation) as exc:
            raise _payload_error() from exc
        if not value.is_finite():
            raise _payload_error()
        parsed.append(
            FredVintage(
                observation_date=observed,
                value=value,
                realtime_start=realtime_start,
                realtime_end=OPEN_ENDED,
            )
        )
    return tuple(parsed)


async def fetch_public_csv(
    client: ResilientHttpClient,
    series_id: str,
    observation_start: date,
    observation_end: date,
    *,
    fetched_at: datetime,
) -> tuple[FredVintage, ...]:
    if series_id not in fred_codes():
        raise ProviderError(
            code="VALIDATION_ERROR",
            message="不在已核对的宏观序列里",
            status_code=422,
        )
    body = await client.get(
        graph_csv_url(
            series_id=series_id,
            observation_start=observation_start,
            observation_end=observation_end,
        )
    )
    return parse_graph_csv(
        body,
        series_id=series_id,
        realtime_start=public_realtime_start(fetched_at),
    )


def _payload_error() -> ProviderError:
    return ProviderError(code="UPSTREAM_PAYLOAD", message="无法解析 FRED 公开 CSV", status_code=502)
