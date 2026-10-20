from datetime import date

import pytest
from app.services.assistant.tool_cache import NEVER_CACHED
from app.services.assistant.tools import default_registry
from app.services.assistant.tools.corpus import CorpusSearchArgs
from pydantic import ValidationError


def test_args_need_at_least_one_real_keyword() -> None:
    assert CorpusSearchArgs(keywords_en=["FOMC"]).keywords_en == ["FOMC"]
    for bad in (
        {},
        {"keywords_zh": ["  "]},
        {"keywords_zh": ["良率", "x" * 41]},
        {"keywords_zh": ["k"] * 9},
        {"keywords_zh": ["良率"], "since": date(2026, 10, 2), "until": date(2026, 10, 1)},
        {"keywords_zh": ["良率"], "kind": "web"},
        {"keywords_zh": ["良率"], "query": "良率"},
    ):
        with pytest.raises(ValidationError):
            CorpusSearchArgs.model_validate(bad)


def test_tool_is_registered_only_when_enabled_and_never_cached() -> None:
    assert "corpus_search" not in default_registry().names
    assert "corpus_search" in default_registry(corpus_min_rank=0.0).names
    assert "corpus_search" in NEVER_CACHED
