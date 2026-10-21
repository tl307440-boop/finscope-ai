def rrf_scores(rankings: list[list[str]], *, k: int = 60) -> dict[str, float]:
    """Reciprocal rank fusion 的分数：每个列表里排第 i 位记 1 / (k + i)。

    同一列表里的重复项只计第一次。
    """
    if k < 1:
        raise ValueError("RRF 的 k 必须为正数")
    scores: dict[str, float] = {}
    for ranking in rankings:
        seen: set[str] = set()
        for index, item in enumerate(ranking, start=1):
            if item in seen:
                continue
            seen.add(item)
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + index)
    return scores


def fuse_rankings(rankings: list[list[str]], *, k: int = 60) -> list[str]:
    """Reciprocal rank fusion. k 默认 60，同一列表里的重复项只计第一次。"""
    scores = rrf_scores(rankings, k=k)
    return sorted(scores, key=lambda item: (-scores[item], item))
