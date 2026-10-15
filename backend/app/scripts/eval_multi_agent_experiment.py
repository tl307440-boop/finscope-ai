"""实验 D 只计算已经提供的标注，不补造 40 家专家结论。"""

from decimal import Decimal


def recall(found: set[str], labeled: set[str]) -> Decimal | None:
    if not labeled:
        return None
    return Decimal(len(found & labeled)) / Decimal(len(labeled))


def hallucination_rate(unsupported: int, statements: int) -> Decimal | None:
    if statements <= 0:
        return None
    return Decimal(unsupported) / Decimal(statements)


PUBLIC_DISCIPLINE = (
    {
        "code": "002717",
        "event_date": "2026-09-18",
        "source": "szse-public",
        "kind": "discipline",
        "note": "规格书记录的公开处分样本，不是专家隐蔽风险标注",
    },
)


def experiment_rows(expert_labels: list[dict[str, str]]) -> list[dict[str, str]]:
    rows = [dict(item) for item in PUBLIC_DISCIPLINE]
    rows.extend(expert_labels)
    return rows


def summary(expert_count: int) -> str:
    missing = 40 - expert_count
    if missing > 0:
        return (
            f"已有公开处分样本和 {expert_count} 条专家标注。"
            f"还缺 {missing} 条，召回率留空，不把未标注公司写成有隐蔽风险。"
        )
    return "40 条专家标注已齐，可以计算召回率。"
