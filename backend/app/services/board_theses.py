"""把东财板块和逻辑追踪里的行业对上，列出当前用户在这些行业下的研究假设。

只按名称匹配（去掉 Ⅱ/Ⅲ 后缀后完全相同，或一方包含另一方），匹配方式随结果返回，界面上如实说明。
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from app.repositories.theses import IndustryRecord, ThesisRecord, ThesisRepository
from app.schemas.industry_market import BoardTheses, LinkedIndustry, LinkedThesis

RULES = [
    "板块与逻辑追踪行业按名称匹配：去掉罗马数字后缀后完全相同记为「同名」，一方包含另一方记为「名称包含」。",
    "假设状态、依据条数由逻辑追踪模块按今天为截止日核对，只统计已入库的公开披露。",
]


def match_industries(
    board_name: str, industries: list[IndustryRecord]
) -> list[tuple[IndustryRecord, str]]:
    plain = board_name.rstrip("ⅡⅢ").strip()
    exact = [(row, "同名") for row in industries if row.name.strip() == plain]
    if exact:
        return exact
    return [
        (row, "名称包含")
        for row in industries
        if row.name.strip() and (row.name.strip() in plain or plain in row.name.strip())
    ]


def link_theses(
    repository: ThesisRepository, user_id: UUID, code: str, board_name: str, as_of: date
) -> BoardTheses:
    linked: list[LinkedIndustry] = []
    for industry, how in match_industries(board_name, repository.list_industries("")):
        theses = repository.list_theses(user_id, industry.id, as_of) or []
        linked.append(
            LinkedIndustry(
                id=str(industry.id),
                name=industry.name,
                match=how,
                theses=[_thesis(row) for row in theses],
            )
        )
    return BoardTheses(board_code=code, board_name=board_name, industries=linked, rules=RULES)


def _thesis(row: ThesisRecord) -> LinkedThesis:
    return LinkedThesis(
        id=str(row.id),
        title=row.title,
        hypothesis=row.hypothesis,
        status=row.status,
        rationale=row.rationale,
        support=sum(1 for item in row.evidence if item.stance == "support"),
        challenge=sum(1 for item in row.evidence if item.stance == "challenge"),
    )
