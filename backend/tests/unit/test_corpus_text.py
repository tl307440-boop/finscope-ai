from datetime import date

from app.services.corpus_text import (
    context_header,
    detect_language,
    en_query_terms,
    index_text,
    ts_config,
    zh_query_terms,
)


def test_language_detection_tolerates_mixed_text() -> None:
    assert detect_language("The Committee decided to maintain the target range.") == "en"
    assert detect_language("沪深300期权成交量 PCR 回落，CPI 同比上涨。") == "zh"
    assert detect_language("") == "en"
    # 英文报告里夹一个中文名，仍按英文处理。
    assert (
        detect_language("Interview with officials at the IMF annual meeting " * 5 + "沃什") == "en"
    )


def test_index_text_keeps_every_bigram() -> None:
    long = "存货跌价" * 50
    tokens = index_text(long, "zh").split()
    assert len(tokens) == 199  # 200 个字 → 199 个二字词，不截断到 32
    assert index_text("Rates rose", "en") == "Rates rose"
    assert (ts_config("en"), ts_config("zh")) == ("english", "simple")


def test_query_terms() -> None:
    assert zh_query_terms(["美联储降息", "PCR", "美联储降息", " "]) == [
        "美联 联储 储降 降息",
        "pcr",
    ]
    assert en_query_terms(["rate cut", "降息", "rate cut"]) == ["rate cut"]


def test_context_header_skips_empty_parts() -> None:
    header = context_header(
        title="2026年前三季度业绩预告",
        publisher="巨潮资讯",
        published=date(2026, 10, 16),
        section="一、本期业绩预计情况",
        symbol="688825",
    )
    assert header == "巨潮资讯｜688825｜2026年前三季度业绩预告｜2026-10-16｜一、本期业绩预计情况"
