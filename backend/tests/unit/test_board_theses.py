import uuid
from datetime import date
from typing import cast
from uuid import UUID

from app.repositories.theses import EvidenceRecord, IndustryRecord, ThesisRecord, ThesisRepository
from app.services.board_theses import link_theses, match_industries

BANK = IndustryRecord(id=uuid.uuid4(), code="sw-bank", name="银行", description="")
LIQUOR = IndustryRecord(id=uuid.uuid4(), code="x-liquor", name="白酒", description="")
FOOD = IndustryRecord(id=uuid.uuid4(), code="sw-food", name="食品饮料", description="")


class FakeTheses:
    def list_industries(self, query: str) -> list[IndustryRecord]:
        return [BANK, LIQUOR, FOOD]

    def list_theses(
        self, user_id: UUID, industry_id: UUID, as_of: date
    ) -> list[ThesisRecord] | None:
        if industry_id != LIQUOR.id:
            return []
        evidence = (
            EvidenceRecord(uuid.uuid4(), "support", "q", date(2026, 9, 1), "https://example.com"),
            EvidenceRecord(uuid.uuid4(), "challenge", "q", date(2026, 9, 2), "https://example.com"),
            EvidenceRecord(uuid.uuid4(), "support", "q", date(2026, 9, 3), "https://example.com"),
        )
        return [
            ThesisRecord(
                uuid.uuid4(), "提价周期", "高端白酒继续提价", "s", "c", "supported", "r", evidence
            )
        ]


def test_exact_name_wins_over_containment() -> None:
    assert match_industries("银行Ⅱ", [BANK, LIQUOR]) == [(BANK, "同名")]
    assert match_industries("白酒Ⅱ", [BANK, LIQUOR, FOOD]) == [(LIQUOR, "同名")]
    assert match_industries("饮料", [BANK, FOOD]) == [(FOOD, "名称包含")]
    assert match_industries("半导体", [BANK, LIQUOR]) == []


def test_link_counts_evidence_by_stance() -> None:
    linked = link_theses(
        cast(ThesisRepository, FakeTheses()), uuid.uuid4(), "BK0477", "白酒Ⅱ", date(2026, 10, 17)
    )
    (industry,) = linked.industries
    (thesis,) = industry.theses
    assert industry.name == "白酒" and industry.match == "同名"
    assert (thesis.support, thesis.challenge, thesis.status) == (2, 1, "supported")
