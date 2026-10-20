"""语料检索评测（知识库设计 §9）：固定语料 + 固定关键词，量 Recall@5、拒答率、私有文档泄露。

不调模型、不联网：关键词按「守规矩的模型」会给的写法写在 `tests/eval/corpus_eval.json` 里。
引用正确率与摘录逐字通过率要真实模型，见 `app/scripts/assistant_corpus_smoke.py`，不进 CI。
评测集是自己写的，满分只说明没有退步；改检索参数后先看打印出来的明细，再决定要不要动下限。
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from app.core.config import get_settings
from app.db.session import create_db_engine
from app.repositories.corpus import CorpusRepository
from app.services.corpus_eval import Loaded, cleanup, load, query_for, read_spec
from app.services.corpus_text import detect_language
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

TOP_K = 5
MIN_RECALL_AT_5 = 0.9
MIN_REFUSAL_RATE = 0.9


def _ready() -> bool:
    engine = create_db_engine(get_settings().database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        root = Path(__file__).resolve().parents[2]
        command.upgrade(Config(str(root / "alembic.ini")), "head")
        return True
    except SQLAlchemyError:
        return False
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def loaded() -> Iterator[Loaded]:
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    engine = create_db_engine(get_settings().database_url)
    yield load(engine, read_spec())
    cleanup(engine)
    engine.dispose()


def _ranked(loaded: Loaded, question: dict[str, Any]) -> list[str]:
    """前 TOP_K 个片段所属的文档，按首次出现排序。"""

    query = query_for(loaded, question, min_rank=get_settings().corpus_min_rank)
    passages = CorpusRepository(loaded.engine).search(query)[:TOP_K]
    return list(dict.fromkeys(loaded.names.get(p.document_id, "?") for p in passages))


def test_retrieval_recall_refusal_and_privacy(loaded: Loaded) -> None:
    recalls: list[float] = []
    refused: list[bool] = []
    leaks: list[str] = []
    lines: list[str] = []
    for question in loaded.spec["questions"]:
        ranked = _ranked(loaded, question)
        expected = question["expect"]
        if expected:
            recall = len(set(expected) & set(ranked)) / len(expected)
            recalls.append(recall)
            mark = "ok  " if recall == 1 else f"{recall:.2f}"
        else:
            refused.append(not ranked)
            mark = "ok  " if not ranked else "MISS"
        if question.get("privacy") and "user-a-notes" in ranked:
            leaks.append(question["id"])
        lines.append(f"{mark} {question['id']} {question['q']} -> {ranked} (expect {expected})")
    recall_at_5 = sum(recalls) / len(recalls)
    refusal_rate = sum(refused) / len(refused)
    report = "\n".join(
        [
            f"Recall@{TOP_K} = {recall_at_5:.3f}（{len(recalls)} 题）",
            f"拒答率 = {refusal_rate:.3f}（{len(refused)} 题）",
            *lines,
        ]
    )
    print(report)
    assert not leaks, f"私有文档泄露：{leaks}\n{report}"
    assert recall_at_5 >= MIN_RECALL_AT_5, report
    assert refusal_rate >= MIN_REFUSAL_RATE, report


def test_eval_set_shape() -> None:
    spec = read_spec()
    ids = {doc["id"] for doc in spec["documents"]}
    questions = spec["questions"]
    assert len({q["id"] for q in questions}) == len(questions)
    assert all(set(q["expect"]) <= ids for q in questions)
    answerable = [q for q in questions if q["expect"]]
    english = {d["id"] for d in spec["documents"] if detect_language("".join(d["pages"])) == "en"}
    # 设计 §9：能答的题里，中文问英文语料的至少一半；另有一批应当拒答的题。
    cross = [q for q in answerable if set(q["expect"]) & english and _has_cjk(q["q"])]
    assert len(cross) * 2 >= len(answerable)
    assert len(questions) - len(answerable) >= 10


def _has_cjk(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)
