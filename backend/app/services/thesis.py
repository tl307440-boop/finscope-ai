from decimal import Decimal

from app.repositories.macro import decimal_text

TAXONOMY_VERSION = "industry-v1"
STATUS_VERSION = "thesis-status-v1"


def fact_quote(concept: str, value: Decimal, unit: str, accession_no: str) -> str:
    return f"{concept} {decimal_text(value)} {unit} 申报号 {accession_no}"


def thesis_status(support_count: int, challenge_count: int) -> tuple[str, str]:
    if support_count < 0 or challenge_count < 0:
        raise ValueError("证据条数不能为负")
    if support_count > 0 and challenge_count > 0:
        return (
            "contested",
            "支持证据和反证都来自已入库记录。这不是已证实的结论。",
        )
    if support_count > 0:
        return ("has_support", "只有支持证据，还不能排除反证。")
    if challenge_count > 0:
        return ("has_challenge", "只有反证，还不能当作假设不成立的终局。")
    return ("open", "还没有可核对的依据。")
