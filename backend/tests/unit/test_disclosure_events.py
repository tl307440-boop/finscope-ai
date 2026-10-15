from datetime import UTC, datetime

from app.services.disclosure_events import (
    ALLOWED_EVENT_TYPES,
    DisclosureDraft,
    NewsEventInput,
    event_hash,
    group_by_day,
    group_by_type,
    heat_label,
    source_label,
    validate_disclosure,
    validate_event_type,
    validate_import,
)

PUBLISHED = datetime(2026, 10, 12, 1, 30, tzinfo=UTC)


def _draft(**overrides: str) -> DisclosureDraft:
    values = {
        "title": "关于回购股份的公告",
        "canonical_url": "https://www.cninfo.com.cn/new/disclosure/detail?anno=1",
        "source_name": "cninfo",
        "event_type": "filing",
    }
    values.update(overrides)
    return DisclosureDraft(published_at=PUBLISHED, **values)


def test_validate_disclosure_accepts_registered_source() -> None:
    assert validate_disclosure(_draft()) is None
    assert source_label("cninfo") == "巨潮资讯"


def test_validate_disclosure_rejects_unknown_source_and_plain_http() -> None:
    assert validate_disclosure(_draft(source_name="some_blog")) is not None
    assert validate_disclosure(_draft(canonical_url="http://example.com/a")) is not None
    assert validate_disclosure(_draft(title="   ")) is not None


def test_validate_event_type_is_a_closed_set() -> None:
    assert validate_event_type("dividend") is None
    assert validate_event_type("rumour") is not None
    assert "dividend" in {code for code, _label in ALLOWED_EVENT_TYPES}


def test_validate_import_requires_usage_rights() -> None:
    item = NewsEventInput(index=1, draft=_draft(), usage_rights="")
    assert validate_import(item) is not None
    allowed = NewsEventInput(index=2, draft=_draft(), usage_rights="public-disclosure")
    assert validate_import(allowed) is None


def test_event_hash_is_stable_and_ignores_padding() -> None:
    first = event_hash(" https://a.example/x ", " 标题 ", PUBLISHED)
    second = event_hash("https://a.example/x", "标题", PUBLISHED)
    assert first == second
    assert len(first) == 64


def test_group_by_day_buckets_titles_by_utc_date() -> None:
    rows = [
        (datetime(2026, 10, 12, 1, 0, tzinfo=UTC), "A"),
        (datetime(2026, 10, 12, 9, 0, tzinfo=UTC), "B"),
        (datetime(2026, 10, 11, 9, 0, tzinfo=UTC), "C"),
    ]
    days = group_by_day(rows)
    assert [item.day for item in days] == ["2026-10-12", "2026-10-11"]
    assert days[0].count == 2
    assert days[0].titles == ("A", "B")


def test_group_by_type_counts_only_known_types_and_sorts_by_count() -> None:
    rows = [("filing", "A"), ("filing", "B"), ("dividend", "C"), ("other", "D")]
    topics = group_by_type(rows)
    assert [(item.event_type, item.count) for item in topics] == [
        ("filing", 2),
        ("dividend", 1),
        ("other", 1),
    ]
    assert topics[0].label == "定期报告"


def test_heat_label_without_events_says_so_instead_of_scoring() -> None:
    assert "暂无" in heat_label(0)
    assert "3" in heat_label(3)
