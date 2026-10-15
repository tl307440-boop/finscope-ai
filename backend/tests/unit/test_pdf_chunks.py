import pytest
from app.services.pdf_chunks import chunk_pages
from app.services.pdf_text import PageText, PdfTextError, extract_pages
from app.services.rrf import fuse_rankings
from app.services.text_embed import embed_text, search_tokens
from tests.unit.pdf_samples import build_text_pdf


def test_chinese_phrase_becomes_bigrams_and_empty_text_has_no_vector() -> None:
    assert "收入" in search_tokens("营业收入")
    assert search_tokens("Revenue locator") == ["revenue", "locator"]
    assert embed_text("   ") is None
    vector = embed_text("营业收入")
    assert vector is not None
    assert len(vector) == 1536


def test_rrf_prefers_a_chunk_found_by_both_lists() -> None:
    ranked = fuse_rankings([["a", "b"], ["b", "c"]], k=60)
    assert ranked[0] == "b"


def test_chunk_keeps_the_page_of_the_matching_sentence() -> None:
    pages = [
        PageText(1, " ".join(["alpha"] * 30)),
        PageText(2, "Revenue locator page two"),
    ]
    chunks = chunk_pages(pages, target_tokens=8, max_tokens=12, overlap_tokens=2)
    matched = [chunk for chunk in chunks if "Revenue locator" in chunk.content]
    assert matched
    assert matched[0].page_end == 2
    assert 1 <= matched[0].page_start <= matched[0].page_end
    assert all(chunk.page_start <= chunk.page_end for chunk in chunks)


def test_extract_pages_reads_each_page_and_rejects_a_broken_file() -> None:
    data = build_text_pdf(["cover only", "Revenue locator page two"])
    pages = extract_pages(data)
    assert [page.page_number for page in pages] == [1, 2]
    assert "Revenue locator page two" in pages[1].text
    with pytest.raises(PdfTextError):
        extract_pages(b"%PDF-1.4 broken")
