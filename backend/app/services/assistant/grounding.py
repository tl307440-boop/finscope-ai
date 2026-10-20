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
7. 检索到的原文片段（来源 [n]）里的数字和日期，只对写了 `[n]` 的句子放行：
   数字是真的、却挂错了出处，照样拦（知识库设计 §7「按引用核对」）。
8. 模型写的东西（情景假设的名字、`quote_table` 摘录表的标题和文字）不进第 3–5 条的放行范围。

返回 `GroundingResult`；`unmatched` 只用于排错和给改写请求，不展示给用户。
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.services.assistant.evidence import EvidenceBook

CITATION = re.compile(r"\[(\d+)\]")
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
# 模型常在数字和「年 / 月 / 日」之间加空格（「9 月」），同样算日期。
_S = r"[ \t\u3000]*"
_DATE_FULL_CN = re.compile(rf"(?<!\d)(\d{{4}}){_S}年\s*(\d{{1,2}}){_S}月\s*(\d{{1,2}}){_S}日")
_DATE_YM_CN = re.compile(rf"(?<!\d)(\d{{4}}){_S}年\s*(\d{{1,2}}){_S}月(?!{_S}\d{{1,2}}{_S}日)")
_DATE_YM_DASH = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})(?![\d-])")
_DATE_MD_CN = re.compile(rf"(?<!\d)(\d{{1,2}}){_S}月\s*(\d{{1,2}}){_S}日")
_DATE_Y_CN = re.compile(rf"(?<!\d)(\d{{4}}){_S}年")
_DATE_M_CN = re.compile(rf"(?<!\d)(\d{{1,2}}){_S}月(?!\d)")
_DATE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (_DATE_FULL_DASH, "ymd"),
    (_DATE_FULL_CN, "ymd"),
    (_DATE_YM_CN, "ym"),
    (_DATE_YM_DASH, "ym"),
    (_DATE_MD_CN, "md"),
    (_DATE_Y_CN, "y"),
    (_DATE_M_CN, "m"),
)


@dataclass
class _Dates:
    """按写出的精度能放行的日期：完整日期、年月、月日、年、月各一层。"""

    ymd: set[tuple[int, ...]] = field(default_factory=set)
    ym: set[tuple[int, ...]] = field(default_factory=set)
    md: set[tuple[int, ...]] = field(default_factory=set)
    y: set[tuple[int, ...]] = field(default_factory=set)
    m: set[tuple[int, ...]] = field(default_factory=set)

    @classmethod
    def of(cls, dates: Iterable[date]) -> "_Dates":
        found = cls()
        for day in dates:
            found.add("ymd", (day.year, day.month, day.day))
        return found

    def add(self, kind: str, parts: tuple[int, ...]) -> None:
        """写出的精度往下都算：片段里有「2026年10月」，「2026年」「10月」也对得上。"""

        if kind == "ymd":
            year, month, day = parts
            self.ymd.add(parts)
            self.ym.add((year, month))
            self.md.add((month, day))
            self.y.add((year,))
            self.m.add((month,))
        elif kind == "ym":
            self.ym.add(parts)
            self.y.add(parts[:1])
            self.m.add(parts[1:])
        elif kind == "md":
            self.md.add(parts)
            self.m.add(parts[:1])
        else:
            getattr(self, kind).add(parts)

    def ok(self, kind: str, parts: tuple[int, ...]) -> bool:
        if kind == "ymd":
            try:
                date(*parts)
            except ValueError:
                return False
        return parts in getattr(self, kind)

    def merged(self, other: "_Dates") -> "_Dates":
        return _Dates(
            self.ymd | other.ymd,
            self.ym | other.ym,
            self.md | other.md,
            self.y | other.y,
            self.m | other.m,
        )


@dataclass(frozen=True)
class GroundingResult:
    ok: bool
    unmatched: tuple[str, ...] = ()
    bad_citations: tuple[int, ...] = ()


class Grounder:
    """对一个回合的证据反复校验；证据登记完再创建，校验期间证据不再变化。"""

    def __init__(self, book: EvidenceBook, sources: frozenset[int] | None = None) -> None:
        self._book = book
        self._numbers = book.numbers()
        self._abs_numbers = [abs(item) for item in self._numbers]
        self._assumed = book.hypothetical_numbers()
        self._abs_assumed = [abs(item) for item in self._assumed]
        self._dates = _Dates.of(book.dates())
        self._cited: dict[int, tuple[list[Decimal], _Dates]] = {}
        # 序列名按去掉空格后的形式比较：列名写「5 年分位」，模型写「5年分位」也是同一个名字。
        self._names = _BLANKS_RE.sub("", book.name_text())
        self._windows = frozenset(
            number + unit for number, unit in _WINDOW_IN_NAME.findall(self._names)
        )
        self._identifiers = _identifiers(book)
        self._sources = book.source_numbers() if sources is None else sources

    def check(self, text: str) -> GroundingResult:
        cited = {int(m) for m in CITATION.findall(text)}
        bad = tuple(sorted(cited - self._sources))
        quoted: list[Decimal] = []
        dates = self._dates
        for number in sorted(cited & self._sources):
            numbers, found = self._passage_facts(number)
            quoted.extend(numbers)
            dates = dates.merged(found)
        body = _EVIDENCE_REF.sub(lambda m: " " * len(m.group()), text)
        body = CITATION.sub(lambda m: " " * len(m.group()), body)
        body = _ORDINAL.sub(lambda m: " " * len(m.group()), body)
        body = _NTH.sub(lambda m: " " * len(m.group()), body)
        unmatched: list[str] = []
        body = _strip_dates(body, dates, unmatched)
        hypothetical = _HYPOTHETICAL.search(body) is not None
        for match in _NUMBER.finditer(body):
            if self._number_ok(body, match, hypothetical=hypothetical, quoted=quoted):
                continue
            unmatched.append(match.group().strip())
        return GroundingResult(
            ok=not unmatched and not bad,
            unmatched=tuple(dict.fromkeys(unmatched)),
            bad_citations=bad,
        )

    def _passage_facts(self, number: int) -> tuple[list[Decimal], _Dates]:
        """来源 [n] 原文里的数字与日期；同一个 Grounder 内只算一次。"""

        known = self._cited.get(number)
        if known is None:
            passage = self._book.source(number)
            content = passage.content if passage is not None else ""
            found = _Dates()
            rest = _scan_dates(content, found)
            known = (_text_numbers(rest), found)
            self._cited[number] = known
        return known

    # ------------------------------------------------------------------ 数字
    def _number_ok(
        self,
        text: str,
        match: re.Match[str],
        *,
        hypothetical: bool,
        quoted: list[Decimal],
    ) -> bool:
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
        if quoted:
            pool = [*pool, *(quoted if signed else (abs(item) for item in quoted))]
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


def check(text: str, book: EvidenceBook, sources: frozenset[int] | None = None) -> GroundingResult:
    return Grounder(book, sources).check(text)


def _strip_dates(text: str, dates: _Dates, unmatched: list[str]) -> str:
    """按精度从长到短找日期，对不上的记进 `unmatched`；处理过的片段换成空格。"""

    for pattern, kind in _DATE_PATTERNS:

        def replace(match: re.Match[str], kind: str = kind) -> str:
            parts = tuple(int(group) for group in match.groups())
            if not dates.ok(kind, parts):
                unmatched.append(match.group())
            return " " * len(match.group())

        text = pattern.sub(replace, text)
    return text


def _scan_dates(text: str, found: _Dates) -> str:
    """把原文里的日期收进 `found`，返回去掉日期之后的文字（剩下的再抽数字）。"""

    for pattern, kind in _DATE_PATTERNS:

        def collect(match: re.Match[str], kind: str = kind) -> str:
            found.add(kind, tuple(int(group) for group in match.groups()))
            return " " * len(match.group())

        text = pattern.sub(collect, text)
    return text


def _text_numbers(text: str) -> list[Decimal]:
    found: list[Decimal] = []
    for match in _NUMBER.finditer(text):
        try:
            value = Decimal(match.group(2).replace(",", ""))
        except InvalidOperation:
            continue
        found.append(-value if match.group(1) else value)
    return found


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
    for item in book.facts():
        keys = [
            column.key for column in item.result.columns if column.kind == "text" and column.visible
        ]
        for row in item.result.rows:
            for key in keys:
                found.update(_DIGITS.findall(row.get(key) or ""))
    return frozenset(found)
