import json
from datetime import UTC, date, datetime

import pytest
from app.providers.disclosures import (
    CninfoStock,
    classify_sec_form,
    classify_title,
    cninfo_query_form,
    parse_cninfo_announcements,
    parse_cninfo_stock_list,
    parse_sec_submissions,
)
from app.providers.errors import ProviderError
from app.services.disclosure_events import validate_disclosure


def test_title_rules_put_inquiries_ahead_of_report_names() -> None:
    assert classify_title("关于2026年半年度报告的问询函回复") == "inquiry"
    assert classify_title("2026年第三季度报告") == "filing"
    assert classify_title("2026年半年度权益分派实施公告") == "dividend"
    assert classify_title("关于控股股东增持股份的公告") == "governance"
    assert classify_title("2026年前三季度业绩预告") == "earnings"
    assert classify_title("关于获得药品注册证书的公告") == "other"


def test_cninfo_announcements_keep_official_link_and_shanghai_time() -> None:
    payload = json.dumps(
        {
            "announcements": [
                {
                    "secName": "贵州茅台",
                    "announcementTitle": "<em>贵州茅台</em>2026年第三季度报告",
                    "announcementTime": 1792122026000,
                    "adjunctUrl": "finalpage/2026-10-16/1225607812.PDF",
                },
                {"secName": "缺链接", "announcementTitle": "无附件", "announcementTime": 1},
            ]
        }
    ).encode()
    drafts = parse_cninfo_announcements(payload)
    assert len(drafts) == 1
    draft = drafts[0]
    assert draft.title == "贵州茅台2026年第三季度报告"
    assert draft.canonical_url == "https://static.cninfo.com.cn/finalpage/2026-10-16/1225607812.PDF"
    assert draft.event_type == "filing"
    assert draft.published_at.tzinfo is UTC
    assert validate_disclosure(draft) is None


def test_cninfo_empty_result_is_not_an_error() -> None:
    assert parse_cninfo_announcements(b'{"announcements": null}') == ()


def test_cninfo_stock_list_maps_code_to_org_id() -> None:
    payload = b'{"stockList":[{"code":"600519","orgId":"gssh0600519"},{"code":""}]}'
    stocks = parse_cninfo_stock_list(payload)
    assert stocks == {"600519": CninfoStock(code="600519", org_id="gssh0600519")}
    form = cninfo_query_form(
        stock=stocks["600519"],
        column="sse",
        start=date(2026, 7, 1),
        end=date(2026, 10, 16),
        page_size=30,
    )
    assert form["stock"] == "600519,gssh0600519"
    assert form["seDate"] == "2026-07-01~2026-10-16"


def test_sec_submissions_filter_forms_and_window() -> None:
    payload = json.dumps(
        {
            "cik": 320193,
            "filings": {
                "recent": {
                    "accessionNumber": ["0000320193-26-000100", "0000320193-26-000090", "x"],
                    "form": ["10-Q", "4", "8-K"],
                    "acceptanceDateTime": [
                        "2026-08-01T16:30:00.000Z",
                        "2026-08-02T16:30:00.000Z",
                        "2025-01-01T16:30:00.000Z",
                    ],
                    "primaryDocument": ["aapl-20260627.htm", "form4.xml", "old.htm"],
                    "primaryDocDescription": ["10-Q", "", ""],
                }
            },
        }
    ).encode()
    drafts = parse_sec_submissions(payload, display_name="苹果", start=date(2026, 1, 1))
    assert [draft.title for draft in drafts] == ["苹果：10-Q"]
    assert drafts[0].canonical_url == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000100/aapl-20260627.htm"
    )
    assert drafts[0].published_at == datetime(2026, 8, 1, 16, 30, tzinfo=UTC)
    assert classify_sec_form("8-K") == "earnings"


def test_malformed_payload_is_structured_error() -> None:
    with pytest.raises(ProviderError) as caught:
        parse_cninfo_stock_list(b"[]")
    assert caught.value.code == "UPSTREAM_PAYLOAD"
