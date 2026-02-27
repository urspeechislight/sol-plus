"""
Tests for Phase 1: INGEST

Tests load the real config/sol.yaml — the single source of truth.
Every test either asserts correct output or asserts that IngestError is raised.
No test asserts that partial output is acceptable.
"""

from __future__ import annotations

import json

import pytest

from src.exceptions import IngestError
from src.models import Manuscript, Page
from src.phases.ingest import ingest
from tests.phases.conftest import SOL_CONFIG as _SOL_CONFIG
_LONG_PAGE_CONTENT = "ن" * (int(_SOL_CONFIG.thresholds["content_page_min_chars"]) * 2)


def _make_book(
    sol_id: str = "test_id_001",
    book_id: int = 1001,
    author: str = "مؤلف",
    title: str = "كتاب",
    book_type: str = "sunni-hadith-general",
    pages: list[dict] | None = None,
    toc: list[dict] | None = None,
) -> dict:
    if pages is None:
        pages = [
            {"page_number": 1, "page_name": " 1 ", "content": "محتوى الصفحة الأولى"},
            {"page_number": 2, "page_name": " 2 ", "content": "محتوى الصفحة الثانية"},
        ]
    return {
        "frontmatter": {
            "sol_id": sol_id,
            "book_id": book_id,
            "author": author,
            "title": title,
            "book_type": book_type,
            "language": "عربي",
            "page_count": len(pages),
        },
        "toc": {str(book_id): toc or []},
        "content": {str(book_id): pages},
    }


class TestIngestSuccess:
    def test_should_return_manuscript_when_valid_book_json(self, tmp_path):
        book = _make_book()
        path = tmp_path / "book.json"
        path.write_text(json.dumps(book), encoding="utf-8")
        result = ingest(path, _SOL_CONFIG)
        assert isinstance(result, Manuscript)

    def test_should_set_work_id_when_sol_id_present(self, tmp_path):
        book = _make_book(sol_id="my_sol_id")
        path = tmp_path / "book.json"
        path.write_text(json.dumps(book), encoding="utf-8")
        result = ingest(path, _SOL_CONFIG)
        assert result.work_id == "my_sol_id"

    def test_should_set_manifestation_id_when_sol_id_present(self, tmp_path):
        book = _make_book(sol_id="my_sol_id")
        path = tmp_path / "book.json"
        path.write_text(json.dumps(book), encoding="utf-8")
        result = ingest(path, _SOL_CONFIG)
        assert result.manifestation_id == "my_sol_id"

    def test_should_populate_all_pages_when_content_block_valid(self, tmp_path):
        pages = [
            {"page_number": i, "page_name": f" {i} ", "content": f"نص {i}"}
            for i in range(1, 4)
        ]
        book = _make_book(pages=pages)
        path = tmp_path / "book.json"
        path.write_text(json.dumps(book), encoding="utf-8")
        result = ingest(path, _SOL_CONFIG)
        assert len(result.pages) == 3

    def test_should_strip_page_name_whitespace_when_ingesting(self, tmp_path):
        pages = [{"page_number": 5, "page_name": "  5  ", "content": "النص"}]
        book = _make_book(pages=pages)
        path = tmp_path / "book.json"
        path.write_text(json.dumps(book), encoding="utf-8")
        result = ingest(path, _SOL_CONFIG)
        assert result.pages[0].page_name == "5"

    def test_should_normalize_text_when_diacritics_present(self, tmp_path):
        pages = [{"page_number": 1, "page_name": " 1 ", "content": "النَّصُّ الكَرِيم"}]
        book = _make_book(pages=pages)
        path = tmp_path / "book.json"
        path.write_text(json.dumps(book), encoding="utf-8")
        result = ingest(path, _SOL_CONFIG)
        assert "َ" not in result.pages[0].text
        assert "ُ" not in result.pages[0].text

    def test_should_normalize_windows_newlines_when_present(self, tmp_path):
        pages = [{"page_number": 1, "page_name": " 1 ", "content": "سطر أول\r\nسطر ثاني"}]
        book = _make_book(pages=pages)
        path = tmp_path / "book.json"
        path.write_text(json.dumps(book), encoding="utf-8")
        result = ingest(path, _SOL_CONFIG)
        assert "\r" not in result.pages[0].text
        assert result.pages[0].text == "سطر أول\nسطر ثاني"

    def test_should_extract_footnote_when_present_on_page(self, tmp_path):
        pages = [{"page_number": 1, "page_name": " 1 ", "content": "النص", "footnote": "الهامش"}]
        book = _make_book(pages=pages)
        path = tmp_path / "book.json"
        path.write_text(json.dumps(book), encoding="utf-8")
        result = ingest(path, _SOL_CONFIG)
        assert result.pages[0].footnote == "الهامش"

    def test_should_set_footnote_none_when_not_present(self, tmp_path):
        pages = [{"page_number": 1, "page_name": " 1 ", "content": "النص"}]
        book = _make_book(pages=pages)
        path = tmp_path / "book.json"
        path.write_text(json.dumps(book), encoding="utf-8")
        result = ingest(path, _SOL_CONFIG)
        assert result.pages[0].footnote is None

    def test_should_include_toc_in_metadata_when_toc_present(self, tmp_path):
        toc = [{"page_number": 5, "page_name": " 5 ", "title": "المقدمة"}]
        book = _make_book(toc=toc)
        path = tmp_path / "book.json"
        path.write_text(json.dumps(book), encoding="utf-8")
        result = ingest(path, _SOL_CONFIG)
        assert result.metadata["toc"] == toc

    def test_should_store_author_and_title_in_metadata(self, tmp_path):
        book = _make_book(author="ابن حجر", title="فتح الباري")
        path = tmp_path / "book.json"
        path.write_text(json.dumps(book), encoding="utf-8")
        result = ingest(path, _SOL_CONFIG)
        assert result.metadata["author"] == "ابن حجر"
        assert result.metadata["title"] == "فتح الباري"

    def test_should_mark_empty_page_non_content_when_text_is_empty(self, tmp_path):
        pages = [{"page_number": 1, "page_name": "1", "content": ""}]
        book = _make_book(pages=pages)
        path = tmp_path / "book.json"
        path.write_text(json.dumps(book), encoding="utf-8")
        result = ingest(path, _SOL_CONFIG)
        assert result.pages[0].is_content is False


class TestIngestFailures:
    def test_should_raise_when_file_does_not_exist(self, tmp_path):
        with pytest.raises(IngestError, match="not found"):
            ingest(tmp_path / "nonexistent.json", _SOL_CONFIG)

    def test_should_raise_when_json_is_malformed(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{ not valid json", encoding="utf-8")
        with pytest.raises(IngestError, match="Cannot parse"):
            ingest(path, _SOL_CONFIG)

    def test_should_raise_when_frontmatter_missing(self, tmp_path):
        book = {"content": {"1001": []}, "toc": {"1001": []}}
        path = tmp_path / "book.json"
        path.write_text(json.dumps(book), encoding="utf-8")
        with pytest.raises(IngestError, match="frontmatter"):
            ingest(path, _SOL_CONFIG)

    def test_should_raise_when_sol_id_missing(self, tmp_path):
        book = _make_book()
        del book["frontmatter"]["sol_id"]
        path = tmp_path / "book.json"
        path.write_text(json.dumps(book), encoding="utf-8")
        with pytest.raises(IngestError, match="sol_id"):
            ingest(path, _SOL_CONFIG)

    def test_should_raise_when_book_id_missing(self, tmp_path):
        book = _make_book()
        del book["frontmatter"]["book_id"]
        path = tmp_path / "book.json"
        path.write_text(json.dumps(book), encoding="utf-8")
        with pytest.raises(IngestError, match="book_id"):
            ingest(path, _SOL_CONFIG)

    def test_should_raise_when_content_block_missing(self, tmp_path):
        book = _make_book()
        del book["content"]
        path = tmp_path / "book.json"
        path.write_text(json.dumps(book), encoding="utf-8")
        with pytest.raises(IngestError, match="content"):
            ingest(path, _SOL_CONFIG)

    def test_should_raise_when_book_id_not_in_content(self, tmp_path):
        book = _make_book(book_id=999)
        book["content"] = {"1234": []}
        path = tmp_path / "book.json"
        path.write_text(json.dumps(book), encoding="utf-8")
        with pytest.raises(IngestError, match="999"):
            ingest(path, _SOL_CONFIG)

    def test_should_raise_when_pages_array_empty(self, tmp_path):
        book = _make_book(pages=[])
        path = tmp_path / "book.json"
        path.write_text(json.dumps(book), encoding="utf-8")
        with pytest.raises(IngestError, match="no pages"):
            ingest(path, _SOL_CONFIG)

    def test_should_raise_when_page_number_not_integer(self, tmp_path):
        pages = [{"page_number": "not_int", "page_name": "x", "content": "y"}]
        book = _make_book(pages=pages)
        path = tmp_path / "book.json"
        path.write_text(json.dumps(book), encoding="utf-8")
        with pytest.raises(IngestError, match="page_number"):
            ingest(path, _SOL_CONFIG)

    def test_should_raise_when_content_field_not_string(self, tmp_path):
        pages = [{"page_number": 1, "page_name": "1", "content": 42}]
        book = _make_book(pages=pages)
        path = tmp_path / "book.json"
        path.write_text(json.dumps(book), encoding="utf-8")
        with pytest.raises(IngestError, match="content is not a string"):
            ingest(path, _SOL_CONFIG)


class TestPageClassification:
    def test_should_mark_early_short_pages_non_content_when_before_threshold(self, tmp_path):
        pages = [
            {"page_number": 1, "page_name": "1", "content": "قصير"},
            {"page_number": 2, "page_name": "2", "content": "قصير"},
            {"page_number": 3, "page_name": "3", "content": _LONG_PAGE_CONTENT},
        ]
        book = _make_book(pages=pages)
        path = tmp_path / "book.json"
        path.write_text(json.dumps(book), encoding="utf-8")
        result = ingest(path, _SOL_CONFIG)
        assert result.pages[0].is_content is False
        assert result.pages[1].is_content is False
        assert result.pages[2].is_content is True

    def test_should_mark_short_line_page_non_content_when_total_chars_exceed_threshold(self, tmp_path):
        """A page with many chars but only short lines (title page pattern) is frontmatter."""
        short_lines = "\n".join([
            "عنوان الكتاب", "المؤلف", "الناشر", "سنة النشر", "الطبعة",
            "بسم الله", "المقدمة", "تمهيد", "فهرس المحتويات", "الجزء الأول",
            "دار النشر", "مكان الطباعة", "رقم الإصدار", "حقوق النشر",
        ])
        assert len(short_lines) > int(_SOL_CONFIG.thresholds["content_page_min_chars"])
        long_line_page = "هذا نص طويل يمثل محتوى حقيقي في صفحة من صفحات الكتاب ويتجاوز الحد الادنى لطول السطر المطلوب ويحتوي على معلومات كافية للتصنيف"
        pages = [
            {"page_number": 1, "page_name": "1", "content": short_lines},
            {"page_number": 2, "page_name": "2", "content": long_line_page},
        ]
        book = _make_book(pages=pages)
        path = tmp_path / "book.json"
        path.write_text(json.dumps(book), encoding="utf-8")
        result = ingest(path, _SOL_CONFIG)
        assert result.pages[0].is_content is False
        assert result.pages[1].is_content is True

    def test_should_mark_empty_page_non_content_when_sandwiched_between_content(self, tmp_path):
        pages = [
            {"page_number": 1, "page_name": "1", "content": _LONG_PAGE_CONTENT},
            {"page_number": 2, "page_name": "2", "content": ""},
            {"page_number": 3, "page_name": "3", "content": _LONG_PAGE_CONTENT},
        ]
        book = _make_book(pages=pages)
        path = tmp_path / "book.json"
        path.write_text(json.dumps(book), encoding="utf-8")
        result = ingest(path, _SOL_CONFIG)
        assert result.pages[1].is_content is False
