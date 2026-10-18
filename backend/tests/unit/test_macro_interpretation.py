from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal

import pytest
from app.core.config import Settings
from app.providers.vintage import VintagePoint
from app.services import macro_interpretation as module
from app.services.macro_interpretation import grounded, interpret

OPEN = date(9999, 12, 31)
PIT = date(2026, 10, 18)


class FakeSource:
    def __init__(self, rows: dict[str, list[VintagePoint]]) -> None:
        self.rows = rows

    def list_vintages_many(
        self,
        series_codes: Sequence[str],
        *,
        since: Mapping[str, date] | None = None,
    ) -> dict[str, list[VintagePoint]]:
        return {code: self.rows.get(code, []) for code in series_codes}


def _point(observed: date, value: str, published: date = date(2020, 1, 1)) -> VintagePoint:
    return VintagePoint(
        observation_date=observed,
        value=Decimal(value),
        unit="Percent",
        realtime_start=published,
        realtime_end=OPEN,
        revision_no=1,
    )


def _settings(configured: bool = True) -> Settings:
    if not configured:
        return Settings(llm_api_key="", llm_api_base_url="", llm_model="")
    return Settings(llm_api_key="key", llm_api_base_url="https://llm.example/v1", llm_model="m-1")


CN_ROWS = {
    "PPI_CN": [_point(date(2026, 8, 1), "4.6"), _point(date(2026, 9, 1), "5.0")],
    "PMI_CN": [_point(date(2026, 9, 1), "49.4")],
}


@pytest.fixture(autouse=True)
def _clean_cache() -> None:
    module.clear_cache()


def test_facts_use_latest_visible_values_and_changes() -> None:
    result = interpret(FakeSource(CN_ROWS), "cn", PIT, _settings(False), lambda *_: "unused")
    by_code = {fact.code: fact for fact in result.facts}
    assert by_code["PPI_CN"].value == "5.00"
    assert by_code["PPI_CN"].change == "+0.40"
    # 只有一期的序列没有「较上期」
    assert by_code["PMI_CN"].previous_value is None
    # 没有任何观测的序列不出现在资料里，也不会被估算
    assert "GDP_CN" not in by_code


def test_us_facts_compute_yoy_and_saar_from_levels() -> None:
    rows = {
        "CPIAUCSL": [
            _point(date(2025, 8, 1), "300"),
            _point(date(2025, 9, 1), "301"),
            _point(date(2026, 8, 1), "309"),
            _point(date(2026, 9, 1), "310.03"),
        ],
        "GDPC1": [_point(date(2026, 1, 1), "100"), _point(date(2026, 4, 1), "101")],
    }
    result = interpret(FakeSource(rows), "us", PIT, _settings(False), lambda *_: "unused")
    by_code = {fact.code: fact for fact in result.facts}
    assert by_code["CPIAUCSL"].value == "3.00"
    assert by_code["CPIAUCSL"].previous_value == "3.00"
    assert by_code["GDPC1"].value == "4.06"


def test_future_publication_is_not_visible() -> None:
    rows = {"PPI_CN": [_point(date(2026, 9, 1), "5.0", published=date(2026, 10, 20))]}
    result = interpret(FakeSource(rows), "cn", PIT, _settings(), lambda *_: "unused")
    assert result.status == "no_data"
    assert result.facts == []


def _record(calls: list[str], prompt: str) -> str:
    calls.append(prompt)
    return ""


def test_no_model_configured_returns_unavailable_with_facts_only() -> None:
    calls: list[str] = []
    result = interpret(
        FakeSource(CN_ROWS),
        "cn",
        PIT,
        _settings(False),
        lambda prompt, _s: _record(calls, prompt),
    )
    assert result.status == "model_unavailable"
    assert result.text is None
    assert result.facts
    assert calls == []


def test_grounded_answer_is_accepted_and_cached() -> None:
    calls: list[str] = []

    def complete(prompt: str, system: str) -> str:
        calls.append(prompt)
        assert "只能使用这些数字" in prompt
        assert "不要预测" in system
        return "PPI同比 2026-09 为 5.00%，较上期变化 +0.40；制造业PMI为 49.4 点。"

    first = interpret(FakeSource(CN_ROWS), "cn", PIT, _settings(), complete, clock=lambda: 0.0)
    second = interpret(FakeSource(CN_ROWS), "cn", PIT, _settings(), complete, clock=lambda: 10.0)
    assert first.status == "ok"
    assert first.text and first.model == "m-1"
    assert second.status == "ok"
    assert len(calls) == 1


def test_cache_expires_after_ttl() -> None:
    calls: list[int] = []

    def complete(_prompt: str, _system: str) -> str:
        calls.append(1)
        return "PPI同比为 5.00%。"

    interpret(FakeSource(CN_ROWS), "cn", PIT, _settings(), complete, clock=lambda: 0.0)
    interpret(FakeSource(CN_ROWS), "cn", PIT, _settings(), complete, clock=lambda: 4000.0)
    assert len(calls) == 2


def test_answer_with_unknown_number_is_rejected_and_not_returned() -> None:
    result = interpret(
        FakeSource(CN_ROWS),
        "cn",
        PIT,
        _settings(),
        lambda *_: "PPI同比约为 5%，预计下季度回落到 3.7%。",
    )
    assert result.status == "rejected"
    assert result.text is None
    assert result.facts


def test_model_failure_and_empty_reply_are_reported() -> None:
    def boom(_prompt: str, _system: str) -> str:
        raise RuntimeError("secret-token leaked?")

    failed = interpret(FakeSource(CN_ROWS), "cn", PIT, _settings(), boom)
    assert failed.status == "error"
    assert "secret" not in failed.message
    empty = interpret(FakeSource(CN_ROWS), "cn", PIT, _settings(), lambda *_: "   ")
    assert empty.status == "error"


def test_grounded_allows_single_digits_and_observation_date_only() -> None:
    facts = interpret(FakeSource(CN_ROWS), "cn", PIT, _settings(False), lambda *_: "").facts
    assert grounded("共 3 项数据，观察日 2026-10-18。", facts, PIT)
    assert not grounded("增长 12 个百分点", facts, PIT)
    assert not grounded("增长 5.01%", facts, PIT)
