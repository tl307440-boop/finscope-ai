import uuid
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.catalog.reference_data import reference_instruments
from app.db.models.industry import (
    Industry,
    IndustryAssessment,
    IndustryMembership,
    IndustryThesis,
    ThesisEvidence,
)
from app.db.models.market import Instrument
from app.db.models.sec import SecFact
from app.repositories.filings import FilingRepository
from app.repositories.instruments import InstrumentRepository
from app.services.thesis import STATUS_VERSION, TAXONOMY_VERSION, fact_quote, thesis_status
from app.utils.identifiers import normalize_cik
from app.utils.time import utc_now

SOURCE = "curated-reference"
VALID_FROM = date(2020, 1, 1)
INDUSTRY_ROWS = (("information-technology", "信息技术", "目录分类，不表示涨跌或收益。"),)


@dataclass(frozen=True)
class IndustryRecord:
    id: UUID
    code: str
    name: str
    description: str


@dataclass(frozen=True)
class FactChoice:
    id: UUID
    quote: str
    filed: date
    source_url: str


@dataclass(frozen=True)
class EvidenceRecord:
    id: UUID
    stance: str
    quote: str
    published_at: date
    source_url: str


@dataclass(frozen=True)
class ThesisRecord:
    id: UUID
    title: str
    hypothesis: str
    support_condition: str
    challenge_condition: str
    status: str
    rationale: str
    evidence: tuple[EvidenceRecord, ...]


class ThesisRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def ensure(self) -> None:
        InstrumentRepository(self.engine).upsert_reference(reference_instruments())
        with Session(self.engine) as session, session.begin():
            for code, name, description in INDUSTRY_ROWS:
                industry = session.scalars(
                    select(Industry).where(Industry.code == code)
                ).one_or_none()
                if industry is None:
                    industry = Industry(
                        id=uuid.uuid4(),
                        code=code,
                        name=name,
                        parent_id=None,
                        level=1,
                        description=description,
                        taxonomy_version=TAXONOMY_VERSION,
                    )
                    session.add(industry)
                    session.flush()
                instrument = session.scalars(
                    select(Instrument).where(
                        Instrument.exchange == "NASDAQ", Instrument.symbol == "AAPL"
                    )
                ).one_or_none()
                if instrument is None:
                    continue
                exists = session.scalars(
                    select(IndustryMembership).where(
                        IndustryMembership.industry_id == industry.id,
                        IndustryMembership.instrument_id == instrument.id,
                        IndustryMembership.valid_from == VALID_FROM,
                    )
                ).one_or_none()
                if exists is None:
                    session.add(
                        IndustryMembership(
                            id=uuid.uuid4(),
                            industry_id=industry.id,
                            instrument_id=instrument.id,
                            source=SOURCE,
                            valid_from=VALID_FROM,
                            valid_to=None,
                        )
                    )

    def list_industries(self, query: str) -> list[IndustryRecord]:
        self.ensure()
        needle = query.strip()
        with Session(self.engine) as session:
            statement = select(Industry).order_by(Industry.name)
            if needle:
                statement = statement.where(Industry.name.contains(needle))
            rows = session.scalars(statement).all()
            return [
                IndustryRecord(id=row.id, code=row.code, name=row.name, description=row.description)
                for row in rows
            ]

    def member_facts(self, industry_id: UUID, as_of: date) -> list[FactChoice]:
        self.ensure()
        with Session(self.engine) as session:
            industry = session.get(Industry, industry_id)
            if industry is None:
                return []
            memberships = session.scalars(
                select(IndustryMembership).where(IndustryMembership.industry_id == industry_id)
            ).all()
            choices: list[FactChoice] = []
            filings = FilingRepository(self.engine)
            for membership in memberships:
                instrument = session.get(Instrument, membership.instrument_id)
                if instrument is None or not instrument.cik:
                    continue
                for fact in filings.list_facts(normalize_cik(instrument.cik), as_of):
                    row = session.get(SecFact, fact.id)
                    if row is None:
                        continue
                    choices.append(
                        FactChoice(
                            id=fact.id,
                            quote=fact_quote(
                                fact.concept, fact.value, fact.unit, fact.accession_no
                            ),
                            filed=fact.filed,
                            source_url=row.source_url,
                        )
                    )
            return choices

    def create_thesis(
        self,
        *,
        user_id: UUID,
        industry_id: UUID,
        title: str,
        hypothesis: str,
        support_condition: str,
        challenge_condition: str,
        observation_window_days: int,
    ) -> ThesisRecord | None:
        self.ensure()
        now = utc_now()
        with Session(self.engine) as session, session.begin():
            if session.get(Industry, industry_id) is None:
                return None
            row = IndustryThesis(
                id=uuid.uuid4(),
                industry_id=industry_id,
                instrument_id=None,
                title=title.strip()[:80],
                hypothesis=hypothesis.strip()[:400],
                support_condition={"statement": support_condition.strip()},
                challenge_condition={"statement": challenge_condition.strip()},
                observation_window_days=observation_window_days,
                created_by=user_id,
                created_at=now,
                updated_at=now,
                status="open",
            )
            session.add(row)
            session.flush()
            return _thesis(row, (), "open", "还没有可核对的依据。")

    def list_theses(
        self, user_id: UUID, industry_id: UUID, as_of: date
    ) -> list[ThesisRecord] | None:
        self.ensure()
        with Session(self.engine) as session:
            if session.get(Industry, industry_id) is None:
                return None
            rows = session.scalars(
                select(IndustryThesis)
                .where(
                    IndustryThesis.industry_id == industry_id,
                    IndustryThesis.created_by == user_id,
                )
                .order_by(IndustryThesis.created_at.desc())
            ).all()
            return [_with_evidence(session, row, as_of) for row in rows]

    def add_fact_evidence(
        self,
        *,
        user_id: UUID,
        thesis_id: UUID,
        fact_id: UUID,
        stance: str,
    ) -> ThesisRecord | None:
        if stance not in {"support", "challenge"}:
            raise ValueError("依据只能是支持或反证")
        with Session(self.engine) as session, session.begin():
            thesis = session.get(IndustryThesis, thesis_id)
            if thesis is None or thesis.created_by != user_id:
                return None
            fact = session.get(SecFact, fact_id)
            if fact is None:
                return None
            filing_repo = FilingRepository(self.engine)
            instrument_ids = session.scalars(
                select(IndustryMembership.instrument_id).where(
                    IndustryMembership.industry_id == thesis.industry_id
                )
            ).all()
            allowed = False
            quote = ""
            source_url = fact.source_url
            published = fact.filed
            for instrument_id in instrument_ids:
                instrument = session.get(Instrument, instrument_id)
                if instrument is None or not instrument.cik:
                    continue
                for item in filing_repo.list_facts(normalize_cik(instrument.cik), fact.filed):
                    if item.id == fact.id:
                        allowed = True
                        quote = fact_quote(item.concept, item.value, item.unit, item.accession_no)
                        published = item.filed
                        source_url = item.source_url
            if not allowed:
                return None
            opposite = session.scalars(
                select(ThesisEvidence).where(
                    ThesisEvidence.thesis_id == thesis_id,
                    ThesisEvidence.fact_id == fact_id,
                )
            ).all()
            if any(row.stance != stance for row in opposite):
                raise ValueError("同一条已入库事实不能同时充当支持和反证")
            if not any(row.stance == stance for row in opposite):
                session.add(
                    ThesisEvidence(
                        id=uuid.uuid4(),
                        thesis_id=thesis_id,
                        fact_id=fact_id,
                        document_id=None,
                        stance=stance,
                        quote_text=quote[:400],
                        published_at=published,
                        source_url=source_url,
                    )
                )
            session.flush()
            return _with_evidence(session, thesis, fact.filed)

    def assess(self, user_id: UUID, thesis_id: UUID, as_of: date) -> ThesisRecord | None:
        now = utc_now()
        with Session(self.engine) as session, session.begin():
            thesis = session.get(IndustryThesis, thesis_id)
            if thesis is None or thesis.created_by != user_id:
                return None
            record = _with_evidence(session, thesis, as_of)
            thesis.status = record.status
            thesis.updated_at = now
            session.add(
                IndustryAssessment(
                    id=uuid.uuid4(),
                    thesis_id=thesis_id,
                    as_of=as_of,
                    status=record.status,
                    evidence_ids=[str(item.id) for item in record.evidence],
                    rationale=record.rationale,
                    model_version=STATUS_VERSION,
                    created_at=now,
                )
            )
            return record


def _with_evidence(session: Session, thesis: IndustryThesis, as_of: date) -> ThesisRecord:
    rows = session.scalars(
        select(ThesisEvidence)
        .where(ThesisEvidence.thesis_id == thesis.id, ThesisEvidence.published_at <= as_of)
        .order_by(ThesisEvidence.published_at, ThesisEvidence.stance)
    ).all()
    evidence = tuple(
        EvidenceRecord(
            id=row.id,
            stance=row.stance,
            quote=row.quote_text,
            published_at=row.published_at,
            source_url=row.source_url,
        )
        for row in rows
    )
    status, rationale = thesis_status(
        sum(1 for item in evidence if item.stance == "support"),
        sum(1 for item in evidence if item.stance == "challenge"),
    )
    return _thesis(thesis, evidence, status, rationale)


def _thesis(
    thesis: IndustryThesis,
    evidence: tuple[EvidenceRecord, ...],
    status: str,
    rationale: str,
) -> ThesisRecord:
    support = thesis.support_condition.get("statement", "")
    challenge = thesis.challenge_condition.get("statement", "")
    return ThesisRecord(
        id=thesis.id,
        title=thesis.title,
        hypothesis=thesis.hypothesis,
        support_condition=support if isinstance(support, str) else "",
        challenge_condition=challenge if isinstance(challenge, str) else "",
        status=status,
        rationale=rationale,
        evidence=evidence,
    )
