"""叙述里的数字、日期、引用校验（规格 §8）。纯函数。

规则（按顺序）：
1. `[n]` 引用必须是已登记的来源编号；`[E1]` 这类证据编号不算数字。
2. 行首序号、`第N` 不算数字。
3. 日期（`YYYY-MM-DD`、`YYYY-MM`、`YYYY年M月D日`、`YYYY年M月`、`M月D日`、`YYYY年`、`M月`）
   按写出的精度，必须落在某条证据的日期里；对不上的日期整段记为不匹配。
4. 剩下的每个数字必须等于某个证据数字，或是它的合理四舍五入（有 d 位小数就按 d 位舍入；
   证据 ≥100 时允许取整）。单位换算（12000 亿写成 1.2 万亿）不放行，要用 `calc` 的 `scale`。
   写出的数字不带负号时按绝对值比较（「下降 0.3」对得上 -0.3），带负号必须逐位同号。
5. 三类放行：紧贴字母的数字（`Q3`、`M2`、`DGS10`）；序列名里的数字（`10年期`、`沪深300`，
   须在证据标题 / 列名里出现）；标识符（证据表里文字列出现过的数字串，如证券代码）。
6. 情景假设（`calc` 的 `assume` 及用它算出的结果）只在句子写明「假设 / 若 / 如果 / 情景」时
   放行，免得假设数字被写成事实。

返回 `GroundingResult`；`unmatched` 只用于排错和给改写请求，不展示给用户。
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.services.assistant.evidence import EvidenceBook

_CITATION = re.compile(r"\[(\d+)\]")
_EVIDENCE_REF = re.compile(r"\[E\d+\]")
_ORDINAL = re.compile(r"(?m)^[ \t]*(?:[-*+][ \t]+)?(?:\d+[.、](?!\d)|[（(]\d+[)）]|\d+[)）])")
_NTH = re.compile(r"第\s*\d+")
_NUMBER = re.compile(
    r"(?<![A-Za-z0-9_.])([-−])?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(\s*(?:%|个百分点|bps|bp|BP))?"
)
_DIGITS = re.compile(r"\d+(?:\.\d+)?")
_CJK_OR_LETTER = re.compile(r"[\u4e00-\u9fffA-Za-z]")
_BLANKS = " \t\u3000"
_BLANKS_RE = re.compile(r"[ \t\u3000]+")
# 列名里的统计窗口：「5 年分位」「36 个月分位」。窗口长度是资料写明的口径，不是数据值。
_WINDOW_IN_NAME = re.compile(r"(\d+)(年|个月)(?=分位)")
_UNIT_AFTER = re.compile(r"[ \t\u3000]*(年|个月)")
_HYPOTHETICAL = re.compile(r"假设|假定|假如|如果|倘若|情景|(?<!倘)若(?!干)")

# 日期按精度从长到短依次处理，处理过的片段替换成空格，避免被后面的规则重复匹配。
_DATE_FULL_DASH = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_DATE_FULL_CN = re.compile(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日")
_DATE_YM_CN = re.compile(r"(\d{4})年\s*(\d{1,2})月(?!\d{1,2}日)")
_DATE_YM_DASH = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})(?![\d-])")
_DATE_MD_CN = re.compile(r"(?<!\d)(\d{1,2})月\s*(\d{1,2})日")
_DATE_Y_CN = re.compile(r"(?<!\d)(\d{4})年")
_DATE_M_CN = re.compile(r"(?<!\d)(\d{1,2})月(?!\d)")


@dataclass(frozen=True)
class GroundingResult:
    ok: bool
    unmatched: tuple[str, ...] = ()
    bad_citations: tuple[int, ...] = ()


class Grounder:
    """对一个回合的证据反复校验；证据登记完再创建，校验期间证据不再变化。"""

    def __init__(self, book: EvidenceBook, sources: frozenset[int] = frozenset()) -> None:
        self._numbers = book.numbers()
        self._abs_numbers = [abs(item) for item in self._numbers]
        self._assumed = book.hypothetical_numbers()
        self._abs_assumed = [abs(item) for item in self._assumed]
        self._dates = book.dates()
        # 序列名按去掉空格后的形式比较：列名写「5 年分位」，模型写「5年分位」也是同一个名字。
        self._names = _BLANKS_RE.sub("", book.name_text())
        self._windows = frozenset(
            number + unit for number, unit in _WINDOW_IN_NAME.findall(self._names)
        )
        self._identifiers = _identifiers(book)
        self._sources = sources

    def check(self, text: str) -> GroundingResult:
        bad = tuple(
            sorted({int(m) for m in _CITATION.findall(text) if int(m) not in self._sources})
        )
        body = _EVIDENCE_REF.sub(lambda m: " " * len(m.group()), text)
        body = _CITATION.sub(lambda m: " " * len(m.group()), body)
        body = _ORDINAL.sub(lambda m: " " * len(m.group()), body)
        body = _NTH.sub(lambda m: " " * len(m.group()), body)
        unmatched: list[str] = []
        body = self._strip_dates(body, unmatched)
        hypothetical = _HYPOTHETICAL.search(body) is not None
        for match in _NUMBER.finditer(body):
            if self._number_ok(body, match, hypothetical=hypothetical):
                continue
            unmatched.append(match.group().strip())
        return GroundingResult(
            ok=not unmatched and not bad,
            unmatched=tuple(dict.fromkeys(unmatched)),
            bad_citations=bad,
        )

    # ------------------------------------------------------------------ 日期
    def _strip_dates(self, text: str, unmatched: list[str]) -> str:
        for pattern, kind in (
            (_DATE_FULL_DASH, "ymd"),
            (_DATE_FULL_CN, "ymd"),
            (_DATE_YM_CN, "ym"),
            (_DATE_YM_DASH, "ym"),
            (_DATE_MD_CN, "md"),
            (_DATE_Y_CN, "y"),
            (_DATE_M_CN, "m"),
        ):
            text = self._strip_one(text, pattern, kind, unmatched)
        return text

    def _strip_one(self, text: str, pattern: re.Pattern[str], kind: str, bad: list[str]) -> str:
        def replace(match: re.Match[str]) -> str:
            parts = tuple(int(group) for group in match.groups())
            if not self._date_ok(kind, parts):
                bad.append(match.group())
            return " " * len(match.group())

        return pattern.sub(replace, text)

    def _date_ok(self, kind: str, parts: tuple[int, ...]) -> bool:
        if kind == "ymd":
            try:
                return date(parts[0], parts[1], parts[2]) in self._dates
            except ValueError:
                return False
        if kind == "ym":
            return any(d.year == parts[0] and d.month == parts[1] for d in self._dates)
        if kind == "md":
            return any(d.month == parts[0] and d.day == parts[1] for d in self._dates)
        if kind == "y":
            return any(d.year == parts[0] for d in self._dates)
        return any(d.month == parts[0] for d in self._dates)

    # ------------------------------------------------------------------ 数字
    def _number_ok(self, text: str, match: re.Match[str], *, hypothetical: bool) -> bool:
        digits = match.group(2)
        if digits in self._identifiers:
            return True
        if self._is_name_number(text, match):
            return True
        try:
            value = Decimal(digits.replace(",", ""))
        except InvalidOperation:
            return False
        signed = match.group(1) is not None
        target = -value if signed else value
        pool = self._numbers if signed else self._abs_numbers
        if hypothetical:
            pool = [*pool, *(self._assumed if signed else self._abs_assumed)]
        exponent = value.as_tuple().exponent
        places = max(0, -exponent) if isinstance(exponent, int) else 0
        return any(_same(target, item, places) for item in pool)

    def _is_name_number(self, text: str, match: re.Match[str]) -> bool:
        start, end = match.start(2), match.end(2)
        for left in range(0, 5):
            for right in range(0, 5):
                if start - left < 0 or end + right > len(text):
                    continue
                outside = text[start - left : start] + text[end : end + right]
                # 空格只是排版：不算「名字里的字」，也不让名字变长。
                letters = [char for char in outside if char not in _BLANKS]
                if len(letters) < 2:
                    continue
                if not all(_CJK_OR_LETTER.match(char) or char in _BLANKS for char in outside):
                    continue
                candidate = _BLANKS_RE.sub("", text[start - left : end + right])
                if candidate in self._names:
                    return True
        return self._is_window(text, match)

    def _is_window(self, text: str, match: re.Match[str]) -> bool:
        """「近 5 年的 32.1% 分位」里的 5 年：资料列名写明了 5 年分位，且这句话说的是分位。"""

        unit = _UNIT_AFTER.match(text, match.end(2))
        if unit is None or "分位" not in text:
            return False
        return match.group(2) + unit.group(1) in self._windows


def check(text: str, book: EvidenceBook, sources: frozenset[int] = frozenset()) -> GroundingResult:
    return Grounder(book, sources).check(text)


def _same(target: Decimal, evidence: Decimal, places: int) -> bool:
    if target == evidence:
        return True
    try:
        if places >= 1:
            quantum = Decimal(1).scaleb(-places)
            return evidence.quantize(quantum, rounding=ROUND_HALF_UP) == target
        return (
            abs(evidence) >= 100 and evidence.quantize(Decimal(1), rounding=ROUND_HALF_UP) == target
        )
    except InvalidOperation:  # 证据数字太大，超出 Decimal 精度：不放行
        return False


def _identifiers(book: EvidenceBook) -> frozenset[str]:
    """证据表里文字列出现过的数字串（证券代码、报告期标签等），不是数值。"""

    found: set[str] = set()
    for item in book:
        keys = [
            column.key for column in item.result.columns if column.kind == "text" and column.visible
        ]
        for row in item.result.rows:
            for key in keys:
                found.update(_DIGITS.findall(row.get(key) or ""))
    return frozenset(found)
