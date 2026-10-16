from dataclasses import dataclass
from datetime import date

# 申万行业分类 2021 版一级行业，按公开分类结果人工整理；只覆盖参考证券池里的 A 股。
TAXONOMY_VERSION = "sw-2021"
SOURCE = "sw2021-curated"
VALID_FROM = date(2021, 7, 30)
DESCRIPTION = "申万一级行业（2021 版），目录分类，不表示涨跌或收益。"


@dataclass(frozen=True)
class IndustrySeed:
    code: str
    name: str
    members: tuple[tuple[str, str], ...]


SW_LEVEL1: tuple[IndustrySeed, ...] = (
    IndustrySeed(
        "sw-food-beverage", "食品饮料", (("SSE", "600519"), ("SZSE", "000858"), ("SZSE", "000568"))
    ),
    IndustrySeed("sw-bank", "银行", (("SZSE", "000001"), ("SSE", "600036"), ("SSE", "601166"))),
    IndustrySeed(
        "sw-non-bank-finance",
        "非银金融",
        (("SSE", "601318"), ("SZSE", "300059"), ("SSE", "600030")),
    ),
    IndustrySeed("sw-power-equipment", "电力设备", (("SZSE", "300750"), ("SSE", "601012"))),
    IndustrySeed("sw-pharma", "医药生物", (("SSE", "600276"), ("SSE", "603259"))),
    IndustrySeed("sw-auto", "汽车", (("SZSE", "002594"),)),
    IndustrySeed("sw-electronics", "电子", (("SZSE", "002475"),)),
    IndustrySeed("sw-computer", "计算机", (("SZSE", "002415"),)),
    IndustrySeed("sw-nonferrous", "有色金属", (("SSE", "601899"),)),
    IndustrySeed("sw-utilities", "公用事业", (("SSE", "600900"),)),
    IndustrySeed("sw-home-appliance", "家用电器", (("SZSE", "000333"),)),
    IndustrySeed("sw-retail", "商贸零售", (("SSE", "601888"),)),
)
