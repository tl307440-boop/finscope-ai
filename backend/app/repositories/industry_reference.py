import uuid

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.catalog.industry_reference import (
    DESCRIPTION,
    SOURCE,
    TAXONOMY_VERSION,
    VALID_FROM,
    IndustrySeed,
)
from app.db.models.industry import Industry, IndustryMembership
from app.db.models.market import Instrument


def upsert_industry_reference(engine: Engine, seeds: tuple[IndustrySeed, ...]) -> tuple[int, int]:
    """写入行业目录与成分归属；已存在的不覆盖。返回（新增行业数，新增归属数）。"""
    industries = 0
    memberships = 0
    with Session(engine) as session, session.begin():
        for seed in seeds:
            industry = session.scalars(
                select(Industry).where(Industry.code == seed.code)
            ).one_or_none()
            if industry is None:
                industry = Industry(
                    id=uuid.uuid4(),
                    code=seed.code,
                    name=seed.name,
                    parent_id=None,
                    level=1,
                    description=DESCRIPTION,
                    taxonomy_version=TAXONOMY_VERSION,
                )
                session.add(industry)
                session.flush()
                industries += 1
            for exchange, symbol in seed.members:
                instrument = session.scalars(
                    select(Instrument).where(
                        Instrument.exchange == exchange, Instrument.symbol == symbol
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
                    memberships += 1
                if instrument.industry_id is None:
                    instrument.industry_id = industry.id
    return industries, memberships
