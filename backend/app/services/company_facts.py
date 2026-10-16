"""公司财务事实的统一读取口径：API、分析、定时快照共用。"""

from datetime import date

from sqlalchemy import Engine

from app.catalog.reference_data import reference_instruments
from app.providers.sec_edgar import load_fixture, parse_companyfacts, payload_sha256
from app.repositories.cn_financials import CnFinancialRepository
from app.repositories.filings import FactRecord, FilingRepository
from app.repositories.instruments import InstrumentRecord, InstrumentRepository
from app.services.ifrs import normalize_facts
from app.utils.identifiers import normalize_cik

LIVE_STATUSES = frozenset({"edgar", "eastmoney"})


def facts_for_instrument(
    engine: Engine, instrument: InstrumentRecord, as_of: date
) -> list[FactRecord]:
    """按市场取财务事实：A 股读东方财富入库的报表，美股读 SEC（没有真实数据时补样例）。"""

    if instrument.market == "CN":
        return CnFinancialRepository(engine).list_facts(instrument.id, as_of)
    if instrument.cik:
        return normalize_facts(visible_sample_facts(engine, normalize_cik(instrument.cik), as_of))
    return []


def visible_sample_facts(engine: Engine, cik: str, as_of: date) -> list[FactRecord]:
    """已从 EDGAR 拉过真实 companyfacts 的公司直接读库；否则补上随包样例。"""
    repository = FilingRepository(engine)
    if repository.has_live_filings(cik):
        return repository.list_facts(cik, as_of)
    InstrumentRepository(engine).upsert_reference(reference_instruments())
    payload = load_fixture()
    parsed = [fact for fact in parse_companyfacts(payload) if fact.cik == cik]
    if parsed:
        repository.upsert_facts(parsed, payload_sha256(payload))
    return repository.list_facts(cik, as_of)


def live_facts(engine: Engine, instrument: InstrumentRecord, as_of: date) -> list[FactRecord]:
    """只取真实披露的事实，不写入、不回退到样例；定时任务用它，没有就是没有。"""

    if instrument.market == "CN":
        rows = CnFinancialRepository(engine).list_facts(instrument.id, as_of)
    elif instrument.cik:
        repository = FilingRepository(engine)
        cik = normalize_cik(instrument.cik)
        rows = (
            normalize_facts(repository.list_facts(cik, as_of))
            if repository.has_live_filings(cik)
            else []
        )
    else:
        rows = []
    return [row for row in rows if row.filing_status in LIVE_STATUSES]
