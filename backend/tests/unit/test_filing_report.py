import json
from datetime import date
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from app.repositories.filings import FactRecord
from app.schemas.research import RESEARCH_NOTICE
from app.services.filing_report import NO_DOCUMENT, compose_filing_report
from app.services.llm_gateway import DISCLAIMER, LanguageModel, ModelReply


class ScriptModel(LanguageModel):
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)

    def complete(self, prompt: str, timeout_seconds: float) -> ModelReply:
        return ModelReply(self.replies.pop(0), 1, 1)


def _fact(value: str, period: date, accession: str) -> FactRecord:
    return FactRecord(
        # id 会写进报告正文；随机 UUID 偶尔含 "999"，会让「模型编的 999 没进报告」的断言随机变红。
        id=uuid5(NAMESPACE_URL, f"{accession}/{period.isoformat()}/{value}"),
        concept="us-gaap:Revenues",
        unit="USD",
        value=Decimal(value),
        period_start=None,
        period_end=period,
        form="10-K",
        fiscal_year=period.year,
        fiscal_period="FY",
        frame=None,
        filed=date(period.year + 1, 2, 1),
        accession_no=accession,
        source_url="https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
    )


def _draft(metric_id: str, claim: str) -> str:
    return json.dumps(
        {
            "analysis_type": "company_filing_review",
            "symbol": "NASDAQ:AAPL",
            "as_of": "2024-06-01T00:00:00Z",
            "data_status": "partial",
            "highlights": [
                {
                    "title": "样例",
                    "claim": claim,
                    "metric_refs": [metric_id],
                    "citation_refs": [],
                    "confidence": "low",
                }
            ],
            "risks": [],
            "missing_information": [],
            "scenario_analysis": [],
            "method_version": "report_v1",
            "disclaimer": DISCLAIMER,
        },
        ensure_ascii=False,
    )


def test_rules_report_uses_only_stored_sample_numbers() -> None:
    earlier = _fact("80", date(2022, 12, 31), "0000320193-23-000001")
    later = _fact("120", date(2023, 12, 31), "0000320193-24-000099")
    draft = compose_filing_report(
        # user_id 固定下来：报告正文里会带上它，随机 UUID 有小概率含 "999"，
        # 那会让下面「模型编的 999 没进报告」这条断言随机变红。
        user_id=UUID("00000000-0000-4000-8000-0000000000ab"),
        symbol="NASDAQ:AAPL",
        name_cn="苹果",
        as_of=date(2024, 6, 1),
        facts=[earlier, later],
        period_a=date(2022, 12, 31),
        period_b=date(2023, 12, 31),
        chunks=[],
        model=None,
        budget_remaining=100,
    )
    assert draft.status == "succeeded"
    assert RESEARCH_NOTICE in draft.body
    assert "80" in draft.body
    assert "120" in draft.body
    assert "0000320193-23-000001" in draft.body
    assert "0000320193-24-000099" in draft.body
    assert NO_DOCUMENT in draft.body
    assert "999" not in draft.body


def test_rejected_model_text_is_not_saved_as_a_finding() -> None:
    earlier = _fact("80", date(2022, 12, 31), "0000320193-23-000001")
    later = _fact("120", date(2023, 12, 31), "0000320193-24-000099")
    model = ScriptModel(
        [
            _draft(str(uuid4()), "引用材料写有 99.9"),
            _draft(str(uuid4()), "引用材料写有 99.9"),
        ]
    )
    draft = compose_filing_report(
        user_id=uuid4(),
        symbol="NASDAQ:AAPL",
        name_cn="苹果",
        as_of=date(2024, 6, 1),
        facts=[earlier, later],
        period_a=date(2022, 12, 31),
        period_b=date(2023, 12, 31),
        chunks=[],
        model=model,
        budget_remaining=100,
    )
    assert draft.status == "failed"
    assert "99.9" not in draft.body
    assert "80" in draft.body
    accepted = compose_filing_report(
        user_id=uuid4(),
        symbol="NASDAQ:AAPL",
        name_cn="苹果",
        as_of=date(2024, 6, 1),
        facts=[earlier, later],
        period_a=date(2022, 12, 31),
        period_b=date(2023, 12, 31),
        chunks=[],
        model=ScriptModel([_draft(str(earlier.id), "引用材料写有 80")]),
        budget_remaining=100,
    )
    assert accepted.status == "succeeded"
    assert "引用材料写有 80" in accepted.body
