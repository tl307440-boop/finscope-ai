import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import cast

from app.providers.errors import ProviderError
from app.providers.http_client import ResilientHttpClient
from app.utils.identifiers import normalize_cik

FIXTURE_NOTICE = "本地样例，不是本次从 SEC 拉取。"
SEC_MIN_INTERVAL_SECONDS = 0.11
_FIXTURE_PATH = Path(__file__).with_name("fixtures") / "sec_companyfacts.json"
_COMPANYFACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"


@dataclass(frozen=True)
class ParsedFact:
    cik: str
    entity_name: str
    concept: str
    unit: str
    value: Decimal
    period_start: date | None
    period_end: date
    form: str
    fiscal_year: int
    fiscal_period: str
    frame: str | None
    filed: date
    accession_no: str
    source_url: str


def companyfacts_url(cik: str) -> str:
    return _COMPANYFACTS.format(cik=normalize_cik(cik))


def load_fixture() -> bytes:
    return _FIXTURE_PATH.read_bytes()


def payload_sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


async def fetch_companyfacts(
    client: ResilientHttpClient,
    cik: str,
    user_agent: str,
) -> bytes:
    if not user_agent.strip():
        raise ProviderError(
            code="DATA_UNAVAILABLE",
            message="未配置 SEC User-Agent，不能访问 EDGAR",
            status_code=503,
        )
    url = companyfacts_url(cik)
    return await client.get(
        url,
        headers={"User-Agent": user_agent, "Accept": "application/json"},
    )


def parse_companyfacts(payload: bytes) -> tuple[ParsedFact, ...]:
    try:
        decoded: object = json.loads(payload, parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _payload_error() from exc
    if not isinstance(decoded, dict):
        raise _payload_error()
    document = cast(dict[str, object], decoded)
    cik = _cik(document.get("cik"))
    entity = document.get("entityName")
    entity_name = entity if isinstance(entity, str) and entity else "未知主体"
    facts = document.get("facts")
    if not isinstance(facts, dict):
        raise _payload_error()
    parsed: list[ParsedFact] = []
    source = companyfacts_url(cik)
    for taxonomy, concepts in cast(dict[str, object], facts).items():
        if not isinstance(concepts, dict):
            continue
        for name, body in cast(dict[str, object], concepts).items():
            parsed.extend(_concept(cik, entity_name, f"{taxonomy}:{name}", body, source))
    return tuple(parsed)


def select_visible(
    facts: tuple[ParsedFact, ...] | list[ParsedFact], as_of: date
) -> list[ParsedFact]:
    """同一概念、单位和期间只保留观察日之前最近一次申报。不按文件里的先后取值。"""

    chosen: dict[tuple[str, str, date, date | None], ParsedFact] = {}
    for fact in facts:
        if fact.filed > as_of:
            continue
        key = (fact.concept, fact.unit, fact.period_end, fact.period_start)
        current = chosen.get(key)
        if current is None or fact.filed > current.filed:
            chosen[key] = fact
    return sorted(chosen.values(), key=lambda item: (item.concept, item.unit, item.filed))


def _concept(
    cik: str,
    entity_name: str,
    concept: str,
    body: object,
    source: str,
) -> list[ParsedFact]:
    if not isinstance(body, dict):
        return []
    units = cast(dict[str, object], body).get("units")
    if not isinstance(units, dict):
        return []
    parsed: list[ParsedFact] = []
    for unit, rows in cast(dict[str, object], units).items():
        if not isinstance(unit, str) or not isinstance(rows, list):
            continue
        for row in rows:
            fact = _row(cik, entity_name, concept, unit, row, source)
            if fact is not None:
                parsed.append(fact)
    return parsed


def _row(
    cik: str,
    entity_name: str,
    concept: str,
    unit: str,
    row: object,
    source: str,
) -> ParsedFact | None:
    if not isinstance(row, dict):
        return None
    record = cast(dict[str, object], row)
    value = _decimal(record.get("val"))
    end = _date(record.get("end"))
    filed = _date(record.get("filed"))
    form = _text(record.get("form"))
    fiscal_period = _text(record.get("fp"))
    accession = _text(record.get("accn"))
    fiscal_year = record.get("fy")
    if (
        value is None
        or end is None
        or filed is None
        or form is None
        or fiscal_period is None
        or accession is None
        or not isinstance(fiscal_year, int)
    ):
        return None
    frame = record.get("frame")
    return ParsedFact(
        cik=cik,
        entity_name=entity_name,
        concept=concept,
        unit=unit,
        value=value,
        period_start=_date(record.get("start")),
        period_end=end,
        form=form,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        frame=frame if isinstance(frame, str) else None,
        filed=filed,
        accession_no=accession,
        source_url=source,
    )


def _cik(value: object) -> str:
    if isinstance(value, int):
        return normalize_cik(str(value))
    if isinstance(value, str):
        return normalize_cik(value)
    raise _payload_error()


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, int | Decimal | str):
        return None
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    return number


def _date(value: object) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _payload_error() -> ProviderError:
    return ProviderError(code="UPSTREAM_PAYLOAD", message="无法解析 SEC 响应", status_code=502)
