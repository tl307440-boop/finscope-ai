"""官方信息披露平台的公告目录：巨潮资讯（A 股）与 SEC EDGAR submissions（美股）。

只读公告元数据（标题、时间、官方原文链接），不下载正文；事件类型按标题关键词归类，
归不进去的记为 other，不猜。
"""

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import cast
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from app.providers.errors import ProviderError
from app.providers.http_client import ResilientHttpClient
from app.services.disclosure_events import DisclosureDraft
from app.utils.identifiers import normalize_cik

CNINFO_STOCK_LIST = "https://www.cninfo.com.cn/new/data/szse_stock.json"
CNINFO_QUERY = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_STATIC = "https://static.cninfo.com.cn/"
SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
USAGE_RIGHTS = "官方信息披露平台公开公告，仅作研究引用，保留原文链接"
SEC_FORMS = frozenset({"10-K", "10-Q", "8-K", "DEF 14A", "S-1", "20-F", "6-K"})
_SHANGHAI = ZoneInfo("Asia/Shanghai")

# 先匹配到的优先：定期报告标题里常带「摘要」「更正」，按出现顺序判断。
_TITLE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("inquiry", ("问询", "关注函", "监管", "警示", "立案", "处罚", "整改")),
    ("earnings", ("业绩预告", "业绩快报", "业绩说明会", "盈利预测")),
    ("filing", ("年度报告", "半年度报告", "季度报告", "年报", "季报")),
    ("dividend", ("分红", "权益分派", "利润分配", "派息", "送转", "现金红利")),
    (
        "governance",
        (
            "股东",
            "董事",
            "监事",
            "高级管理",
            "回购",
            "增持",
            "减持",
            "质押",
            "章程",
            "股权激励",
            "股份",
            "选举",
            "辞职",
        ),
    ),
)


@dataclass(frozen=True)
class CninfoStock:
    code: str
    org_id: str


def classify_title(title: str) -> str:
    for event_type, keywords in _TITLE_RULES:
        if any(keyword in title for keyword in keywords):
            return event_type
    return "other"


def classify_sec_form(form: str) -> str:
    if form in {"10-K", "10-Q", "20-F"}:
        return "filing"
    if form == "8-K":
        return "earnings"
    if form == "DEF 14A":
        return "governance"
    return "other"


def parse_cninfo_stock_list(payload: bytes) -> dict[str, CninfoStock]:
    document = _json(payload)
    rows = document.get("stockList")
    if not isinstance(rows, list):
        raise _payload_error("巨潮证券列表")
    stocks: dict[str, CninfoStock] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        item = cast(dict[str, object], raw)
        code, org_id = item.get("code"), item.get("orgId")
        if isinstance(code, str) and isinstance(org_id, str) and code and org_id:
            stocks[code] = CninfoStock(code=code, org_id=org_id)
    return stocks


def cninfo_query_form(
    *, stock: CninfoStock, column: str, start: date, end: date, page_size: int
) -> dict[str, str]:
    return {
        "pageNum": "1",
        "pageSize": str(page_size),
        "column": column,
        "tabName": "fulltext",
        "stock": f"{stock.code},{stock.org_id}",
        "seDate": f"{start.isoformat()}~{end.isoformat()}",
        "isHLtitle": "true",
    }


@dataclass(frozen=True)
class MarketAnnouncement:
    announcement_id: str
    code: str
    name: str
    title: str
    url: str
    published_at: datetime


def cninfo_market_form(
    *, start: date, end: date, page: int, category: str = "", searchkey: str = ""
) -> dict[str, str]:
    """全市场查询：stock 留空、column=szse 时覆盖沪深京全部 A 股，单页最多 30 条。"""

    return {
        "pageNum": str(page),
        "pageSize": "30",
        "column": "szse",
        "tabName": "fulltext",
        "stock": "",
        "searchkey": searchkey,
        "category": f"{category};" if category else "",
        "seDate": f"{start.isoformat()}~{end.isoformat()}",
        "isHLtitle": "true",
    }


def parse_cninfo_market(payload: bytes) -> tuple[MarketAnnouncement, ...]:
    document = _json(payload)
    rows = document.get("announcements")
    if rows is None:
        return ()
    if not isinstance(rows, list):
        raise _payload_error("巨潮公告")
    found: list[MarketAnnouncement] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        item = cast(dict[str, object], raw)
        title = _clean_title(item.get("announcementTitle"))
        adjunct, stamp = item.get("adjunctUrl"), item.get("announcementTime")
        code, name, ident = item.get("secCode"), item.get("secName"), item.get("announcementId")
        if not (
            title
            and isinstance(adjunct, str)
            and isinstance(stamp, int)
            and isinstance(code, str)
            and isinstance(name, str)
        ):
            continue
        found.append(
            MarketAnnouncement(
                announcement_id=str(ident) if ident is not None else adjunct,
                code=code,
                name=_clean_title(name),
                title=title,
                url=CNINFO_STATIC + adjunct.lstrip("/"),
                published_at=datetime.fromtimestamp(stamp / 1000, tz=_SHANGHAI).astimezone(UTC),
            )
        )
    return tuple(found)


def parse_cninfo_announcements(payload: bytes) -> tuple[DisclosureDraft, ...]:
    document = _json(payload)
    rows = document.get("announcements")
    if rows is None:
        return ()
    if not isinstance(rows, list):
        raise _payload_error("巨潮公告")
    drafts: list[DisclosureDraft] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        item = cast(dict[str, object], raw)
        title = _clean_title(item.get("announcementTitle"))
        adjunct = item.get("adjunctUrl")
        stamp = item.get("announcementTime")
        name = item.get("secName")
        if not title or not isinstance(adjunct, str) or not isinstance(stamp, int):
            continue
        prefix = f"{name}：" if isinstance(name, str) and name and name not in title else ""
        drafts.append(
            DisclosureDraft(
                title=f"{prefix}{title}",
                canonical_url=CNINFO_STATIC + adjunct.lstrip("/"),
                source_name="cninfo",
                published_at=datetime.fromtimestamp(stamp / 1000, tz=_SHANGHAI).astimezone(UTC),
                event_type=classify_title(title),
            )
        )
    return tuple(drafts)


def parse_sec_submissions(
    payload: bytes, *, display_name: str, start: date
) -> tuple[DisclosureDraft, ...]:
    document = _json(payload)
    cik_raw = document.get("cik")
    filings = document.get("filings")
    if not isinstance(filings, dict):
        raise _payload_error("SEC submissions")
    recent = cast(dict[str, object], filings).get("recent")
    if not isinstance(recent, dict):
        raise _payload_error("SEC submissions")
    columns = cast(dict[str, object], recent)
    accessions = _column(columns, "accessionNumber")
    forms = _column(columns, "form")
    accepted = _column(columns, "acceptanceDateTime")
    documents = _column(columns, "primaryDocument")
    descriptions = _column(columns, "primaryDocDescription")
    cik = str(int(normalize_cik(str(cik_raw))))
    drafts: list[DisclosureDraft] = []
    for index, form in enumerate(forms):
        if form not in SEC_FORMS or index >= min(len(accessions), len(accepted), len(documents)):
            continue
        try:
            moment = datetime.fromisoformat(accepted[index].replace("Z", "+00:00"))
        except ValueError:
            continue
        if moment.date() < start or not documents[index]:
            continue
        detail = descriptions[index] if index < len(descriptions) else ""
        title = f"{display_name}：{form}" + (f" {detail}" if detail and detail != form else "")
        drafts.append(
            DisclosureDraft(
                title=title,
                canonical_url=SEC_ARCHIVE.format(
                    cik=cik,
                    accession=accessions[index].replace("-", ""),
                    document=documents[index],
                ),
                source_name="sec",
                published_at=moment.astimezone(UTC),
                event_type=classify_sec_form(form),
            )
        )
    return tuple(drafts)


async def fetch_cninfo_stock_list(client: ResilientHttpClient) -> dict[str, CninfoStock]:
    return parse_cninfo_stock_list(await client.get(CNINFO_STOCK_LIST))


async def fetch_cninfo_announcements(
    client: ResilientHttpClient,
    *,
    stock: CninfoStock,
    exchange: str,
    start: date,
    end: date,
    page_size: int = 30,
) -> tuple[DisclosureDraft, ...]:
    column = "sse" if exchange == "SH" else "szse"
    body = urlencode(
        cninfo_query_form(stock=stock, column=column, start=start, end=end, page_size=page_size)
    ).encode()
    payload = await client.post(
        CNINFO_QUERY,
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
    )
    return parse_cninfo_announcements(payload)


async def fetch_sec_submissions(
    client: ResilientHttpClient, *, cik: str, display_name: str, user_agent: str, start: date
) -> tuple[DisclosureDraft, ...]:
    if not user_agent.strip():
        raise ProviderError(
            code="DATA_UNAVAILABLE",
            message="未配置 SEC User-Agent，不能访问 EDGAR",
            status_code=503,
        )
    payload = await client.get(
        SEC_SUBMISSIONS.format(cik=normalize_cik(cik)),
        headers={"User-Agent": user_agent, "Accept": "application/json"},
    )
    return parse_sec_submissions(payload, display_name=display_name, start=start)


def _clean_title(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.replace("<em>", "").replace("</em>", "").strip()


def _column(columns: dict[str, object], key: str) -> list[str]:
    values = columns.get(key)
    if not isinstance(values, list):
        return []
    return [item if isinstance(item, str) else "" for item in values]


def _json(payload: bytes) -> dict[str, object]:
    try:
        decoded: object = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _payload_error("响应") from exc
    if not isinstance(decoded, dict):
        raise _payload_error("响应")
    return cast(dict[str, object], decoded)


def _payload_error(what: str) -> ProviderError:
    return ProviderError(code="UPSTREAM_PAYLOAD", message=f"无法解析{what}", status_code=502)
