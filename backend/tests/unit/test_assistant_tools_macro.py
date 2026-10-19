"""宏观序列工具（规格 §7）：时点换算、截断、过期、参数校验。用假仓库，不碰数据库。"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import uuid4

import pytest
from app.providers.vintage import VintagePoint
from app.services.assistant.tools import ToolRegistry, default_registry
from app.services.assistant.tools import macro as macro_tools
from app.services.assistant.tools.base import ToolContext
from sqlalchemy import Engine

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)  # 上海 10-19 20:00，纽约 10-19 08:00


class FakeMacroRepository:
    data: dict[str, list[VintagePoint]] = {}

    def __init__(self, engine: object) -> None:
        del engine

    def list_vintages(
        self, series_code: str, *, start: date | None, end: date | None
    ) -> list[VintagePoint] | None:
        points = FakeMacroRepository.data.get(series_code)
        if points is None:
            return None
        return [
            point
            for point in points
            if (start is None or point.observation_date >= start)
            and (end is None or point.observation_date <= end)
        ]


def _point(observed: date, value: str, published: date) -> VintagePoint:
    return VintagePoint(
        observation_date=observed,
        value=Decimal(value),
        unit="Percent",
        realtime_start=published,
        realtime_end=date.max,
        revision_no=1,
    )


def _monthly(count: int = 5, last: date = date(2026, 9, 1)) -> list[VintagePoint]:
    months = [date(last.year, last.month, 1)]
    for _ in range(count - 1):
        previous = months[-1] - timedelta(days=1)
        months.append(previous.replace(day=1))
    months.reverse()
    return [
        _point(month, f"{2 + index / 10:.1f}0", month + timedelta(days=40))
        for index, month in enumerate(months)
    ]


@pytest.fixture(autouse=True)
def fake_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeMacroRepository.data = {}
    monkeypatch.setattr(macro_tools, "MacroRepository", FakeMacroRepository)


def _ctx(now: datetime = NOW) -> ToolContext:
    return ToolContext(engine=cast(Engine, None), user_id=uuid4(), now=now)


def _run(registry: ToolRegistry, name: str, args: str, ctx: ToolContext | None = None) -> Any:
    return registry.execute(name, args, ctx or _ctx())


def test_us_series_returns_visible_rows_with_unit_and_source() -> None:
    FakeMacroRepository.data["CPIAUCSL"] = _monthly()
    FakeMacroRepository.data["UNRATE"] = _monthly()
    assert (
        _run(default_registry(), "macro_series", '{"code": "UNRATE"}').result.columns[1].unit == "%"
    )
    outcome = _run(default_registry(), "macro_series", '{"code": "cpiaucsl"}')
    result = outcome.result
    assert result.status == "ok" and result.source == "FRED"
    assert result.title == "美国消费者物价指数"
    assert [row["observation_date"] for row in result.rows][-1] == "2026-09-01"
    assert result.rows[-1]["value"] == "2.4"  # 末尾的 0 被去掉
    assert result.primary == "value" and result.as_of == date(2026, 10, 19)
    assert outcome.args == {"code": "CPIAUCSL"}


def test_historical_as_of_hides_later_publications() -> None:
    FakeMacroRepository.data["CPIAUCSL"] = _monthly()
    # 2026-09-01 的观测 10-11 才公布；as_of 在公布日当天仍不可见（发布日当天不算已公布）
    outcome = _run(
        default_registry(), "macro_series", '{"code": "CPIAUCSL", "as_of": "2026-10-11"}'
    )
    assert outcome.result.rows[-1]["observation_date"] == "2026-08-01"
    later = _run(default_registry(), "macro_series", '{"code": "CPIAUCSL", "as_of": "2026-10-12"}')
    assert later.result.rows[-1]["observation_date"] == "2026-09-01"


def test_as_of_in_the_future_is_capped_to_today() -> None:
    FakeMacroRepository.data["CPIAUCSL"] = _monthly()
    outcome = _run(
        default_registry(), "macro_series", '{"code": "CPIAUCSL", "as_of": "2030-01-01"}'
    )
    assert outcome.result.as_of == date(2026, 10, 19)


def test_market_calendar_decides_the_point_in_time() -> None:
    # 世界时 10-18 20:00：上海已是 10-19 04:00，纽约还是 10-18 16:00
    now = datetime(2026, 10, 18, 20, 0, tzinfo=UTC)
    published = date(2026, 10, 18)
    FakeMacroRepository.data["CPI_CN"] = [_point(date(2026, 9, 1), "0.9", published)]
    FakeMacroRepository.data["CPIAUCSL"] = [_point(date(2026, 9, 1), "3.1", published)]
    china = _run(default_registry(), "macro_series", '{"code": "CPI_CN"}', _ctx(now))
    us = _run(default_registry(), "macro_series", '{"code": "CPIAUCSL"}', _ctx(now))
    assert china.result.status == "ok" and china.result.as_of == date(2026, 10, 19)
    assert us.result.status == "unavailable" and us.result.as_of == date(2026, 10, 18)
    assert "没有已公布的观测" in (us.result.reason or "")


def test_stale_series_is_flagged_with_the_latest_observation_date() -> None:
    FakeMacroRepository.data["UNRATE"] = _monthly(last=date(2026, 5, 1))
    outcome = _run(default_registry(), "macro_series", '{"code": "UNRATE"}')
    assert outcome.result.status == "stale"
    assert "2026-05-01" in (outcome.result.reason or "")
    assert outcome.result.rows  # 偏旧也照样给数据


def test_rows_are_capped_at_120_and_report_truncation() -> None:
    first = date(2026, 1, 1)
    FakeMacroRepository.data["DGS10"] = [
        _point(first + timedelta(days=index), "4.25", first + timedelta(days=index))
        for index in range(0, 130)
    ]
    outcome = _run(
        default_registry(),
        "macro_series",
        '{"code": "DGS10", "start": "2026-01-01", "end": "2026-10-17", "as_of": "2026-10-17"}',
    )
    assert len(outcome.result.rows) == 120
    assert outcome.result.truncated == 10
    assert outcome.result.rows[-1]["observation_date"] == "2026-05-10"
    # 最后一条观测离 as_of 很远，同时是 stale
    assert outcome.result.status == "stale"


def test_missing_series_and_empty_visible_window_are_unavailable() -> None:
    registry = default_registry()
    assert _run(registry, "macro_series", '{"code": "UNRATE"}').result.reason == (
        "美国失业率还没有入库观测"
    )
    FakeMacroRepository.data["UNRATE"] = [_point(date(2026, 9, 1), "4.3", date(2026, 10, 18))]
    early = _run(registry, "macro_series", '{"code": "UNRATE", "as_of": "2026-10-01"}')
    assert early.result.status == "unavailable" and "2026-10-01" in (early.result.reason or "")


@pytest.mark.parametrize(
    ("args", "needle"),
    [
        ('{"code": "NOPE"}', "没有这条序列"),
        ("{}", "code"),
        ('{"code": "UNRATE", "start": "2026-05-01", "end": "2026-01-01"}', "start 不能晚于 end"),
        ('{"code": "UNRATE", "start": "2000-01-01", "end": "2026-01-01"}', "不能超过 10 年"),
        ('{"code": "UNRATE", "extra": 1}', "extra"),
        ('{"code": "UNRATE", "as_of": "not-a-date"}', "as_of"),
        ("not json", "不是合法的 JSON"),
        ("[1]", "必须是 JSON 对象"),
    ],
)
def test_invalid_arguments_become_unavailable_not_exceptions(args: str, needle: str) -> None:
    outcome = _run(default_registry(), "macro_series", args)
    assert outcome.result.status == "unavailable"
    assert needle in (outcome.result.reason or "")


def test_unknown_tool_is_reported() -> None:
    outcome = _run(default_registry(), "drop_database", "{}")
    assert outcome.result.status == "unavailable"
    assert "没有这个工具" in (outcome.result.reason or "")


def test_compare_aligns_common_observation_dates() -> None:
    FakeMacroRepository.data["CPIAUCSL"] = _monthly(count=5)
    FakeMacroRepository.data["UNRATE"] = _monthly(count=4)  # 少最早一个月
    outcome = _run(default_registry(), "macro_compare", '{"codes": ["CPIAUCSL", "UNRATE"]}')
    result = outcome.result
    assert result.status == "ok" and len(result.rows) == 4
    assert [column.key for column in result.columns] == ["observation_date", "value_a", "value_b"]
    assert result.rows[0]["observation_date"] == "2026-06-01"


def test_compare_rejects_different_frequencies_and_bad_pairs() -> None:
    FakeMacroRepository.data["CPIAUCSL"] = _monthly()
    FakeMacroRepository.data["GDP"] = [_point(date(2026, 4, 1), "30000", date(2026, 7, 30))]
    mixed = _run(default_registry(), "macro_compare", '{"codes": ["CPIAUCSL", "GDP"]}')
    assert mixed.result.status == "unavailable"
    assert "频率不同" in (mixed.result.reason or "")
    same = _run(default_registry(), "macro_compare", '{"codes": ["GDP", "gdp"]}')
    assert "不能相同" in (same.result.reason or "")
    one = _run(default_registry(), "macro_compare", '{"codes": ["GDP"]}')
    assert one.result.status == "unavailable"


def test_tool_definitions_are_sorted_and_byte_stable() -> None:
    first = default_registry().definitions()
    second = default_registry().definitions()
    assert first == second
    names = [item["function"]["name"] for item in first]
    assert names == sorted(names)
    assert {"macro_series", "calc", "price_bars", "custom_indicator", "watchlist"} <= set(names)
    schema = next(item for item in first if item["function"]["name"] == "calc")["function"]
    assert "$defs" not in str(schema) and "$ref" not in str(schema)
    assert "anyOf" not in str(schema) and '"title"' not in str(schema).replace("'", '"')
