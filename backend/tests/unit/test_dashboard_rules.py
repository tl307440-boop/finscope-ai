"""总览驾驶舱评分规则：用 2026-10-16 收盘后真实抓取的输入做回归基准，另测缺数据与边界。"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from app.services import dashboard_rules as rules

FIXTURE = Path(__file__).parent / "fixtures" / "dashboard_20261016.json"


@pytest.fixture(scope="module")
def sample() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return loaded


def _num(value: object) -> Decimal:
    return Decimal(str(value))


def _obs(rows: list[list[object]]) -> list[rules.Observation]:
    return [rules.Observation(date.fromisoformat(str(day)), _num(value)) for day, value in rows]


def _amounts(sample: dict[str, Any]) -> list[Decimal]:
    shenzhen = {day: value for day, value in sample["amount_sz_wan"]}
    return [
        (_num(value) + _num(shenzhen[day])) * 10000
        for day, value in sample["amount_sh_wan"]
        if day in shenzhen
    ]


def _temperature(sample: dict[str, Any], **overrides: Any) -> rules.Temperature:
    arguments: dict[str, Any] = {
        "buckets": {int(key): value for key, value in sample["fenbu"].items()},
        "amounts": _amounts(sample),
        "limit_up": sample["limit_up"],
        "broken": sample["broken"],
        "closes": [_num(close) for _, close in sample["csi_all_closes"]],
    }
    arguments.update(overrides)
    return rules.market_temperature(**arguments)


def test_temperature_golden_values(sample: dict[str, Any]) -> None:
    result = _temperature(sample)
    assert result.result.score == Decimal("38.9")
    assert result.result.label == "中性偏弱"
    scores = {factor.code: factor.score for factor in result.result.factors}
    assert scores == {
        "trend": Decimal("0.97"),
        "breadth": Decimal("66.57"),
        "activity": Decimal("35.49"),
        "volatility": Decimal("55.00"),
    }
    assert result.breadth == rules.Breadth(
        up=3197, down=2058, flat=115, total=5370, strong=563, weak=376, limit_up=61, limit_down=10
    )
    assert result.activity is not None
    assert result.activity.amount == Decimal("1555420779700")
    assert rules.round_to(result.activity.ratio, 4) == Decimal("0.8996")
    assert result.activity.seal_rate is not None
    assert rules.round_to(result.activity.seal_rate, 2) == Decimal("55.45")
    assert result.volatility is not None and result.volatility.windows == 240
    assert result.result.missing == ()


def test_temperature_drops_missing_breadth_and_renormalises(sample: dict[str, Any]) -> None:
    result = _temperature(sample, buckets=None)
    assert [factor.code for factor in result.result.factors] == ["trend", "activity", "volatility"]
    assert result.result.coverage == Decimal("0.65")
    expected = (Decimal("0.97") * 30 + Decimal("35.49") * 20 + Decimal("55.00") * 15) / 65
    assert result.result.score == rules.round_to(expected, 1)
    assert any("广度" in item for item in result.result.missing)


def test_temperature_refuses_to_score_when_most_inputs_are_missing() -> None:
    result = rules.market_temperature(
        buckets={1: 10, -1: 5}, amounts=[], limit_up=None, broken=None, closes=[]
    )
    assert result.result.score is None
    assert result.result.label == "数据不足"
    assert len(result.result.missing) == 3


def test_intraday_volume_ratio_scales_the_baseline(sample: dict[str, Any]) -> None:
    amounts = _amounts(sample)
    half = rules.activity_from(amounts, 61, 49, rules.session_fraction(120))
    full = rules.activity_from(amounts, 61, 49)
    assert half is not None and full is not None
    assert half.ratio == full.ratio * 2
    assert rules.session_fraction(0) == 0 and rules.session_fraction(300) == 1


def test_activity_without_pool_uses_volume_only(sample: dict[str, Any]) -> None:
    result = _temperature(sample, limit_up=None, broken=None)
    activity = next(factor for factor in result.result.factors if factor.code == "activity")
    assert activity.score == Decimal("37.45")
    assert "缺" in activity.formula


def test_liquidity_golden_values(sample: dict[str, Any]) -> None:
    fred = {key: _obs(rows) for key, rows in sample["fred"].items()}
    liquidity = rules.global_liquidity(
        rules.LiquidityInputs(
            walcl=fred["WALCL"],
            tga=fred["WTREGEN"],
            rrp=fred["RRPONTSYD"],
            dollar=fred["DTWEXBGS"],
            real_yield=fred["DFII10"],
            hy_spread=fred["BAMLH0A0HYM2"],
            nfci=fred["NFCI"],
            shibor_on=_obs(sample["shibor_on"]),
            yields=[
                rules.YieldPoint(date.fromisoformat(day), _num(cn), _num(us))
                for day, cn, us in sample["treasury"]
            ],
        )
    )
    assert liquidity.result.score == Decimal("42.2")
    assert liquidity.result.label == "中性偏紧"
    assert {factor.code: factor.score for factor in liquidity.result.factors} == {
        "fed_net": Decimal("51.98"),
        "DTWEXBGS": Decimal("21.33"),
        "DFII10": Decimal("40.00"),
        "BAMLH0A0HYM2": Decimal("23.50"),
        "NFCI": Decimal("74.95"),
        "SHIBOR_ON": Decimal("53.72"),
        "CN_US_10Y": Decimal("20.64"),
    }


def test_liquidity_with_only_china_inputs_is_not_scored(sample: dict[str, Any]) -> None:
    liquidity = rules.global_liquidity(rules.LiquidityInputs(shibor_on=_obs(sample["shibor_on"])))
    assert liquidity.result.score is None
    assert liquidity.result.coverage == Decimal("0.15")
    assert len(liquidity.result.missing) == 6


def test_net_liquidity_uses_latest_tga_and_rrp_on_or_before_each_week() -> None:
    walcl = [rules.Observation(date(2026, 10, 7), Decimal(6_600_000))]
    tga = [
        rules.Observation(date(2026, 10, 6), Decimal(700_000)),
        rules.Observation(date(2026, 10, 8), Decimal(1)),
    ]
    rrp = [rules.Observation(date(2026, 10, 7), Decimal(20))]
    assert rules.net_liquidity(walcl, tga, rrp) == (
        rules.Observation(date(2026, 10, 7), Decimal(5880)),
    )
    assert rules.net_liquidity(walcl, [], rrp) == ()


def test_growth_and_regime_golden_values(sample: dict[str, Any]) -> None:
    fred = {key: _obs(rows) for key, rows in sample["fred"].items()}
    growth = rules.global_growth(
        unemployment=fred["UNRATE"],
        payrolls=fred["PAYEMS"],
        china_pmi=_obs(sample["china_pmi"]),
        cpi=fred["CPIAUCSL"],
    )
    assert growth.result.score == Decimal("46.7")
    assert growth.result.label == "中性"
    assert growth.regime == "通胀回升"
    assert growth.cpi_yoy is not None and rules.round_to(growth.cpi_yoy, 2) == Decimal("3.72")
    assert growth.inflation_momentum is not None
    assert rules.round_to(growth.inflation_momentum, 3) == Decimal("0.254")


@pytest.mark.parametrize(
    ("growth", "momentum", "regime"),
    [
        (Decimal(60), Decimal("0.1"), "扩张伴随通胀升温"),
        (Decimal(60), Decimal("-0.1"), "温和扩张"),
        (Decimal(40), Decimal("0.1"), "滞胀压力"),
        (Decimal(40), Decimal(0), "增长放缓"),
        (Decimal(50), Decimal("0.1"), "平稳"),
        (Decimal(50), Decimal("-0.3"), "平稳降温"),
        (None, Decimal(1), "无法判断"),
    ],
)
def test_macro_regime_matrix(growth: Decimal | None, momentum: Decimal, regime: str) -> None:
    assert rules.macro_regime(growth, momentum) == regime


def test_board_heat_golden_values(sample: dict[str, Any]) -> None:
    plates = [
        rules.PlateInput(
            code=row["code"],
            name=row["name"],
            kind=kind,
            heat=None if row["heat"] is None else _num(row["heat"]),
            change_pct=None if row["change_pct"] is None else _num(row["change_pct"]),
            tag=row["tag"],
            hot_tag=row["hot_tag"],
        )
        for key, kind in (("ths_concept", "概念"), ("ths_industry", "行业"))
        for row in sample[key]
    ]
    heat = rules.board_heat(plates)
    assert [(row.plate.name, row.index) for row in heat[:5]] == [
        ("飞行汽车(eVTOL)", 99),
        ("低空经济", 88),
        ("军工", 82),
        ("军工装备", 74),
        ("汽车零部件", 72),
    ]
    assert sum(row.hot for row in heat) == 5


def test_board_heat_handles_missing_heat_and_change() -> None:
    heat = rules.board_heat(
        [
            rules.PlateInput("1", "甲", "概念", Decimal(100), Decimal(2), tag="12家涨停"),
            rules.PlateInput("2", "乙", "概念", None, None),
        ]
    )
    assert heat[0].limit_ups == 12 and heat[0].limit_score == 100 and heat[0].hot
    assert heat[1].index == 0 and not heat[1].hot
    assert rules.board_heat([]) == []


def test_watch_state_and_signals() -> None:
    assert rules.watch_state(Decimal(3)) == "强势"
    assert rules.watch_state(Decimal("-3")) == "弱势"
    assert rules.watch_state(Decimal("2.99")) == "震荡"
    assert rules.watch_state(None) is None
    assert rules.watch_signals(
        change_pct=Decimal(10), limit_status="涨停", streak=3, ths_hot_rank=8
    ) == [("rise", "3连板"), ("violet", "热榜#8")]
    assert rules.watch_signals(
        change_pct=Decimal("-6"), limit_status=None, streak=None, ths_hot_rank=None
    ) == [("fall", "异动")]


@pytest.mark.parametrize(
    ("title", "kind", "level"),
    [
        ("关于控股股东部分股份解除质押的公告", "质押", 1),
        ("关于收到中国证监会立案告知书的公告", "风险", 3),
        ("2026年前三季度业绩预告", "业绩", 3),
        ("关于重大资产重组的进展公告", "重组", 3),
        ("北京某律师事务所关于重大资产重组的法律意见书", "常规", 0),
        ("2026年第三季度报告", "定期报告", 2),
        ("关于股东减持股份计划的公告", "减持", 2),
        ("关于召开临时股东大会的通知", "其他", 0),
    ],
)
def test_classify_announcement(title: str, kind: str, level: int) -> None:
    found = rules.classify_announcement(title)
    assert (found.kind, found.level) == (kind, level)


def test_stock_heat_keeps_the_strongest_reason() -> None:
    heat = rules.stock_heat(
        ths_ranks=[("600001", 5), ("600002", 60)],
        limit_up=[("600001", 2), ("600003", 3)],
        limit_down=["600004"],
        gainers=["600002", "600005"],
    )
    assert heat["600001"] == rules.StockHeat(3, "同花顺热榜第 5")
    assert heat["600002"].level == 2
    assert heat["600003"] == rules.StockHeat(1, "3连板")
    assert heat["600004"].reason == "跌停"
    assert heat["600005"].reason == "涨幅榜前 40"


def test_clean_title_strips_code_and_name_prefix() -> None:
    assert rules.clean_title("贵州茅台：关于回购的公告", "600519", "贵州茅台") == "关于回购的公告"
    assert rules.clean_title("关于回购的公告", "600519", "贵州茅台") == "关于回购的公告"
