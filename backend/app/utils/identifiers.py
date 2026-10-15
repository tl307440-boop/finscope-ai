from dataclasses import dataclass

_SUFFIX_TO_EXCHANGE = {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}
_EXCHANGE_TO_SUFFIX = {"SSE": "SH", "SZSE": "SZ", "BSE": "BJ"}


@dataclass(frozen=True)
class ListedCode:
    market: str
    exchange: str
    symbol: str
    display_symbol: str

    @property
    def canonical_code(self) -> str:
        return f"{self.exchange}:{self.symbol}"


@dataclass(frozen=True)
class SearchKey:
    """搜索键。六位数字不会被猜成某一个交易所。"""

    raw: str
    exchange: str | None = None
    symbol: str | None = None
    cik: str | None = None
    text: str | None = None


def canonical_code(exchange: str, symbol: str) -> str:
    return f"{exchange}:{symbol}"


def display_symbol_for(exchange: str, symbol: str) -> str:
    suffix = _EXCHANGE_TO_SUFFIX.get(exchange)
    if suffix is None:
        return symbol
    return f"{symbol}.{suffix}"


def normalize_cik(value: str) -> str:
    digits = value.strip()
    if not digits.isdigit() or not 1 <= len(digits) <= 10:
        raise ValueError("CIK 必须是 1 到 10 位数字")
    return digits.zfill(10)


def parse_listed_code(value: str) -> ListedCode | None:
    text = value.strip().upper().replace(" ", "")
    if not text:
        return None
    if ":" in text:
        exchange, _, symbol = text.partition(":")
        if exchange in _EXCHANGE_TO_SUFFIX and symbol.isdigit() and len(symbol) == 6:
            return _cn(exchange, symbol)
        return None
    if "." in text:
        symbol, _, suffix = text.partition(".")
        exchange = _SUFFIX_TO_EXCHANGE.get(suffix)
        if exchange and symbol.isdigit() and len(symbol) == 6:
            return _cn(exchange, symbol)
        return None
    if len(text) == 8 and text[:2] in _SUFFIX_TO_EXCHANGE and text[2:].isdigit():
        return _cn(_SUFFIX_TO_EXCHANGE[text[:2]], text[2:])
    return None


def parse_search_key(value: str) -> SearchKey:
    raw = value.strip()
    listed = parse_listed_code(raw)
    if listed is not None:
        return SearchKey(raw=raw, exchange=listed.exchange, symbol=listed.symbol)
    compact = raw.strip()
    if compact.isdigit():
        if len(compact) == 6:
            return SearchKey(raw=raw, symbol=compact, cik=normalize_cik(compact))
        return SearchKey(raw=raw, cik=normalize_cik(compact))
    return SearchKey(raw=raw, text=compact)


def _cn(exchange: str, symbol: str) -> ListedCode:
    return ListedCode(
        market="CN",
        exchange=exchange,
        symbol=symbol,
        display_symbol=display_symbol_for(exchange, symbol),
    )
