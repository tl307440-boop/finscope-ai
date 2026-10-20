import json
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from app.providers.disclosures import CninfoStock
from app.providers.polite import Exchange, PoliteClient, PoliteError
from app.repositories.corpus import Passage
from app.repositories.instruments import InstrumentRecord
from app.services.assistant.tools.base import ToolContext
from app.services.assistant.tools.filings import FilingFetchArgs, FilingFetchTool
from app.services.cninfo_filings import CninfoCatalog
from app.services.filing_meta import FilingHit, period_of, report_type_of
from app.services.pdf_text import PageText
from app.services.sec_filings import exhibit_name, html_to_text

NOW = datetime(2026, 10, 20, 4, tzinfo=UTC)
PDF_URL = "https://static.cninfo.com.cn/finalpage/2026-10-12/a.pdf"


def test_titles_map_to_report_type_and_period() -> None:
    assert report_type_of("2026年前三季度业绩预告") == "forecast"
    assert period_of("2026年前三季度业绩预告") == "2026Q3"
    assert report_type_of("2026年半年度业绩快报") == "express"
    assert period_of("2026年半年度业绩快报") == "2026H1"
    assert report_type_of("关于问询函的回复") == "inquiry"
    assert report_type_of("股东减持股份计划") == "other"
    assert period_of("股东减持股份计划") is None


class _Script:
    def __init__(self, responses: list[Exchange]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method: str, url: str, headers: object, content: object) -> Exchange:
        self.calls.append((method, url))
        return self.responses.pop(0)


def _catalog(script: _Script) -> CninfoCatalog:
    client = PoliteClient(
        user_agent="x",
        allowed_hosts=frozenset({"www.cninfo.com.cn", "static.cninfo.com.cn"}),
        opener=script,
        resolve=lambda _host: ("1.1.1.1",),
        sleeper=lambda _seconds: None,
        clock=lambda: 0,
    )
    return CninfoCatalog(client, stocks={"688825": CninfoStock("688825", "org1")})


def _announcement(title: str) -> bytes:
    return json.dumps(
        {
            "announcements": [
                {
                    "announcementTitle": title,
                    "adjunctUrl": "finalpage/2026-10-12/a.pdf",
                    "announcementTime": 1760280000000,
                    "secCode": "688825",
                    "secName": "示例新材",
                    "announcementId": "1",
                }
            ]
        }
    ).encode()


def test_catalog_lists_a_forecast_and_only_downloads_the_static_host() -> None:
    script = _Script([Exchange(200, {}, _announcement("2026年前三季度业绩预告"))])
    hits = _catalog(script).list_stock(
        code="688825", exchange="SH", start=date(2026, 7, 1), end=date(2026, 10, 20)
    )
    assert [(hit.report_type, hit.period, hit.url) for hit in hits] == [
        ("forecast", "2026Q3", PDF_URL)
    ]
    assert script.calls[0][0] == "POST"


def test_download_refuses_a_url_outside_the_static_host() -> None:
    catalog = _catalog(_Script([]))
    with pytest.raises(PoliteError) as caught:
        catalog.download("https://www.cninfo.com.cn/not-a-file")
    assert caught.value.code == "HOST"


def _stock() -> InstrumentRecord:
    return InstrumentRecord(
        id=uuid4(),
        market="CN",
        exchange="SH",
        symbol="688825",
        display_symbol="688825.SH",
        name_cn="示例新材",
        name_en="",
        instrument_type="equity",
        currency="CNY",
        cik=None,
    )


def _stored() -> Passage:
    return Passage(
        chunk_id=uuid4(),
        document_id=uuid4(),
        title="2026年前三季度业绩预告",
        publisher="巨潮资讯",
        kind="filing",
        doc_type="announcement",
        url=PDF_URL,
        published_at=datetime(2026, 10, 12, 10, tzinfo=UTC),
        content="预计净利润为 1.2 亿元至 1.5 亿元。",
        rank=0,
    )


def _tool(catalog: CninfoCatalog, **kwargs: object) -> FilingFetchTool:
    return FilingFetchTool(
        catalog,
        resolve=lambda _ctx, _raw: _stock(),
        extract=lambda _pdf, **_extra: [
            PageText(page_number=1, text="预计净利润为 1.2 亿元至 1.5 亿元。")
        ],
        clock=lambda: 0,
        **kwargs,  # type: ignore[arg-type]
    )


def _ctx() -> ToolContext:
    return ToolContext(engine=None, user_id=uuid4(), now=NOW)  # type: ignore[arg-type]


def test_fetch_stores_the_pdf_and_returns_its_passages() -> None:
    script = _Script(
        [Exchange(200, {}, _announcement("2026年前三季度业绩预告")), Exchange(200, {}, b"%PDF")]
    )
    saved: list[object] = []
    seen = {"url": ""}

    def load(url: str) -> list[Passage]:
        seen["url"] = url
        return [_stored()] if saved else []

    tool = _tool(
        _catalog(script), load=load, store=lambda hit, pages, instrument: saved.append(hit)
    )
    result = tool.run(
        _ctx(), FilingFetchArgs(instrument_id="x", report_type="forecast", period="2026Q3")
    )
    assert result.status == "ok"
    assert result.passages[0].content.startswith("预计净利润")
    assert saved and result.rows[0]["url"] == PDF_URL
    assert [call[0] for call in script.calls] == ["POST", "GET"]


def test_an_ingested_filing_is_not_downloaded_again() -> None:
    script = _Script([Exchange(200, {}, _announcement("2026年前三季度业绩预告"))])
    tool = _tool(_catalog(script), load=lambda _url: [_stored()], store=lambda *_a: None)
    result = tool.run(_ctx(), FilingFetchArgs(instrument_id="x"))
    assert result.status == "ok"
    assert [call[0] for call in script.calls] == ["POST"]


def test_over_budget_hands_the_filing_to_the_worker() -> None:
    script = _Script([Exchange(200, {}, _announcement("2026年前三季度业绩预告"))])
    ticks = iter([0, 30])
    queued: list[str] = []
    tool = FilingFetchTool(
        _catalog(script),
        resolve=lambda _ctx, _raw: _stock(),
        clock=lambda: next(ticks),
        load=lambda _url: [],
        enqueue=lambda hit: queued.append(hit.url),
    )
    result = tool.run(_ctx(), FilingFetchArgs(instrument_id="x"))
    assert result.status == "unavailable"
    assert "稍后再问" in (result.reason or "")
    assert queued == [PDF_URL]


def _apple() -> InstrumentRecord:
    return InstrumentRecord(
        id=uuid4(),
        market="US",
        exchange="NASDAQ",
        symbol="AAPL",
        display_symbol="AAPL",
        name_cn="",
        name_en="Apple",
        instrument_type="equity",
        currency="USD",
        cik="0000320193",
    )


def test_a_us_stock_without_a_cik_is_refused() -> None:
    stock = _apple()
    stock = InstrumentRecord(
        id=stock.id,
        market="US",
        exchange="NASDAQ",
        symbol="AAPL",
        display_symbol="AAPL",
        name_cn="",
        name_en="Apple",
        instrument_type="equity",
        currency="USD",
        cik=None,
    )
    result = FilingFetchTool(resolve=lambda _ctx, _raw: stock).run(
        _ctx(), FilingFetchArgs(instrument_id="x")
    )
    assert "CIK" in (result.reason or "")


def test_eight_k_keeps_the_exhibit_text_and_drops_scripts() -> None:
    primary = "https://www.sec.gov/Archives/edgar/data/320193/0001/d8k.htm"
    hit = FilingHit("AAPL", "Apple：8-K", primary, datetime(2026, 10, 12, tzinfo=UTC), "8-K", None)

    class _Sec:
        def list_8k(self, **_kwargs: object) -> tuple[FilingHit, ...]:
            return (hit,)

        def download_body(self, url: str) -> tuple[str, bytes]:
            assert url == primary
            return url, b"<html><script>bad()</script><p>Revenue was 10 million.</p></html>"

    saved: list[FilingHit] = []
    tool = FilingFetchTool(
        resolve=lambda _ctx, _raw: _apple(),
        sec=_Sec(),
        clock=lambda: 0,
        load=lambda _url: [_stored()] if saved else [],
        store=lambda _hit, _pages, _instrument: saved.append(_hit),
    )
    result = tool.run(_ctx(), FilingFetchArgs(instrument_id="x"))
    assert result.status == "ok" and result.source == "美国证券交易委员会"
    assert saved and saved[0].url == primary
    assert "bad" not in html_to_text(b"<script>bad()</script><p>Revenue was 10 million.</p>")


def test_exhibit_name_prefers_the_press_release_and_rejects_paths() -> None:
    payload = json.dumps(
        {
            "directory": {
                "item": [
                    {"name": "d8k.htm", "type": "8-K"},
                    {"name": "ex99.htm", "type": "EX-99.1"},
                    {"name": "../evil.htm", "type": "EX-99.1"},
                ]
            }
        }
    ).encode()
    assert exhibit_name(payload) == "ex99.htm"
    assert exhibit_name(b"not json") is None


def test_missing_contact_email_blocks_a_live_catalog() -> None:
    tool = FilingFetchTool(resolve=lambda _ctx, _raw: _stock())
    result = tool.run(_ctx(), FilingFetchArgs(instrument_id="x"))
    assert result.status == "unavailable"
    assert "联系邮箱" in (result.reason or "")
