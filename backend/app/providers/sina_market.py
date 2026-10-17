"""新浪财经公开行情：行业 / 概念板块涨幅、沪深 A 股排行、日经 225 与外盘期货。

只做 URL 构造与解析。东财 push2 被频控时作为后备源；页面上按实际来源标注。

口径（2026-10 实测）：
- 板块 `newSinaHy.php`（行业）/ `newFLJK.php?param=class`（概念）：GBK 的 JS 对象，值为逗号串
  `代码,名称,家数,均价,涨跌额,涨跌幅%,成交量,成交额(元),领涨代码,领涨涨幅%,领涨价,领涨涨跌,领涨名称`。
  板块涨跌幅为成分等权均值。
- 排行 `Market_Center.getHQNodeData`：JSON，changepercent 为 %，amount 为元，per 为动态市盈率。
- `hq.sinajs.cn`：`znb_NKY` 1 最新价、2 涨跌额；`hf_GC`/`hf_CL` 0 最新价、7 昨结算、12 日期。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import cast

from app.providers.errors import ProviderError

INDUSTRY_URL = "https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"
CONCEPT_URL = "https://vip.stock.finance.sina.com.cn/q/view/newFLJK.php?param=class"
HQ_URL = "https://hq.sinajs.cn/list="
SINA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (FinScope research)",
    "Referer": "https://finance.sina.com.cn/",
}
_RANK_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData"
)
_HQ_LINE = re.compile(r'hq_str_(\w+)="([^"]*)"')


@dataclass(frozen=True)
class SinaBoard:
    code: str
    name: str
    members: int
    change_pct: Decimal | None
    amount: Decimal | None
    leader_code: str | None
    leader_name: str | None
    leader_change_pct: Decimal | None


@dataclass(frozen=True)
class SinaStock:
    code: str
    name: str
    last: Decimal | None
    change_pct: Decimal | None
    amount: Decimal | None
    turnover_pct: Decimal | None
    pe_dynamic: Decimal | None


@dataclass(frozen=True)
class SinaQuote:
    symbol: str
    last: Decimal | None
    prev_close: Decimal | None
    quote_time: str


def rank_url(sort: str, count: int) -> str:
    if sort not in ("changepercent", "amount"):
        raise ProviderError(code="VALIDATION_ERROR", message="不支持的排序字段", status_code=422)
    return f"{_RANK_URL}?page=1&num={count}&sort={sort}&asc=0&node=hs_a&symbol=&_s_r_a=page"


def hq_url(symbols: list[str] | tuple[str, ...]) -> str:
    return HQ_URL + ",".join(symbols)


def parse_boards(payload: bytes) -> tuple[SinaBoard, ...]:
    text = payload.decode("gbk", errors="replace")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise _payload_error("板块")
    try:
        document = cast(object, json.loads(text[start : end + 1]))
    except json.JSONDecodeError as exc:
        raise _payload_error("板块") from exc
    if not isinstance(document, dict):
        raise _payload_error("板块")
    boards: list[SinaBoard] = []
    for value in cast(dict[str, object], document).values():
        parts = str(value).split(",")
        if len(parts) < 13 or not parts[2].isdigit():
            continue
        leader = parts[8].strip()
        boards.append(
            SinaBoard(
                code=parts[0],
                name=parts[1].strip(),
                members=int(parts[2]),
                change_pct=_number(parts[5]),
                amount=_number(parts[7]),
                leader_code=leader[2:] if len(leader) == 8 else None,
                leader_name=parts[12].replace(" ", "") or None,
                leader_change_pct=_number(parts[9]),
            )
        )
    return tuple(boards)


def parse_rank(payload: bytes) -> tuple[SinaStock, ...]:
    try:
        document = cast(object, json.loads(payload.decode("utf-8", errors="replace")))
    except json.JSONDecodeError as exc:
        raise _payload_error("排行") from exc
    if not isinstance(document, list):
        raise _payload_error("排行")
    rows: list[SinaStock] = []
    for raw in cast(list[object], document):
        if not isinstance(raw, dict):
            continue
        row = cast(dict[str, object], raw)
        code, name = row.get("code"), row.get("name")
        if not isinstance(code, str) or not isinstance(name, str):
            continue
        rows.append(
            SinaStock(
                code=code,
                name=name.replace(" ", ""),
                last=_number(row.get("trade")),
                change_pct=_number(row.get("changepercent")),
                amount=_number(row.get("amount")),
                turnover_pct=_number(row.get("turnoverratio")),
                pe_dynamic=_number(row.get("per")),
            )
        )
    return tuple(rows)


def parse_hq(payload: bytes) -> dict[str, SinaQuote]:
    quotes: dict[str, SinaQuote] = {}
    for symbol, body in _HQ_LINE.findall(payload.decode("gbk", errors="replace")):
        parts = body.split(",")
        if symbol.startswith("znb_") and len(parts) > 7:
            last, change = _number(parts[1]), _number(parts[2])
            prev = None if last is None or change is None else last - change
            quotes[symbol] = SinaQuote(symbol, last, prev, f"{parts[6]} {parts[7]}")
        elif symbol.startswith("hf_") and len(parts) > 12:
            quotes[symbol] = SinaQuote(
                symbol, _number(parts[0]), _number(parts[7]), f"{parts[12]} {parts[6]}"
            )
    return quotes


def _number(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text or text in ("-", "--"):
        return None
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def _payload_error(label: str) -> ProviderError:
    return ProviderError(
        code="UPSTREAM_PAYLOAD", message=f"无法解析新浪{label}响应", status_code=502
    )
