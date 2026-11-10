"""日线因子与横截面排名。纯函数、无副作用，全程 Decimal。

因子只描述「截至最后一根日线」的历史特征，不是收益预测：
- momentum：最近 window 个交易日的涨跌幅（%）
- volatility：最近 window 个交易日日收益率标准差，按 252 日年化（%）
- drawdown：最近 window 个交易日内的最大回撤（%）
"""

from collections.abc import Sequence
from decimal import Decimal
from typing import Literal

FactorName = Literal["momentum", "volatility", "drawdown"]
FACTORS: tuple[FactorName, ...] = ("momentum", "volatility", "drawdown")
_ANNUAL = Decimal(252).sqrt()
_QUANT = Decimal("0.01")


def factor_value(closes: Sequence[Decimal], factor: FactorName, window: int) -> Decimal | None:
    """closes 是按时间升序的收盘价；样本不够返回 None。"""

    if window < 2 or len(closes) < window + 1:
        return None
    tail = list(closes[-(window + 1) :])
    if any(price <= 0 for price in tail):
        return None
    if factor == "momentum":
        return ((tail[-1] / tail[0] - 1) * 100).quantize(_QUANT)
    if factor == "volatility":
        rets = [tail[i] / tail[i - 1] - 1 for i in range(1, len(tail))]
        mean = sum(rets, Decimal(0)) / len(rets)
        var = sum(((r - mean) ** 2 for r in rets), Decimal(0)) / (len(rets) - 1)
        return (var.sqrt() * _ANNUAL * 100).quantize(_QUANT)
    peak = tail[0]
    worst = Decimal(0)
    for price in tail:
        peak = max(peak, price)
        worst = max(worst, (peak - price) / peak)
    return (worst * 100).quantize(_QUANT)


def rank_values(
    values: dict[str, Decimal], descending: bool = True
) -> list[tuple[int, str, Decimal]]:
    """(名次, key, 值)；同值并列同名次，key 排序保证结果稳定。"""

    ordered = sorted(values.items(), key=lambda kv: (-kv[1] if descending else kv[1], kv[0]))
    out: list[tuple[int, str, Decimal]] = []
    for index, (key, value) in enumerate(ordered, 1):
        rank = out[-1][0] if out and out[-1][2] == value else index
        out.append((rank, key, value))
    return out
