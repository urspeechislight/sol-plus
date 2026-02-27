"""
Phase 1: INGEST

Input:  path to book.json
Output: Manuscript with pages and metadata populated

Loads the JSON, extracts work identity from frontmatter, processes pages
(normalizing text and extracting footnotes), classifies content pages vs.
frontmatter/backmatter, and reads TOC entries.

This phase knows nothing about Arabic patterns or behavior labels.
Its only job is to understand the source format and produce clean pages.

See src/phases/CLAUDE.md for the full intent and failure contract.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from src.exceptions import IngestError
from src.models import Manuscript, Page
from src.utils.config import Config
from src.utils.text import normalize

logger = logging.getLogger(__name__)


def ingest(book_path: str | Path, config: Config) -> Manuscript:
    """Load a manuscript JSON file and return a Manuscript ready for Phase 2.

    Raises IngestError if the file is missing, malformed, or structurally invalid.
    Never returns a partially-constructed Manuscript.
    """
    path = Path(book_path)
    if not path.exists():
        raise IngestError(f"Book file not found: {path}")

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise IngestError(f"Cannot parse {path.name}: {exc}") from exc

    return _build_manuscript(raw, path.name, config)


def _build_manuscript(raw: dict, source_name: str, config: Config) -> Manuscript:
    frontmatter = _require(raw, "frontmatter", source_name)
    book_id = str(_require(frontmatter, "book_id", source_name))

    metadata = _extract_metadata(frontmatter, source_name)
    pages = _extract_pages(raw, book_id, source_name, config)
    toc = _extract_toc(raw, book_id)

    manuscript = Manuscript(
        work_id=metadata["work_id"],
        manifestation_id=metadata["manifestation_id"],
        metadata={**metadata, "toc": toc, "source_file": source_name},
    )
    manuscript.pages = pages

    logger.info(
        "Ingested %s: %d pages (%d content)",
        source_name,
        len(pages),
        sum(1 for p in pages if p.is_content),
    )
    return manuscript


def _extract_metadata(frontmatter: dict, source_name: str) -> dict:
    sol_id = _require(frontmatter, "sol_id", source_name)
    return {
        "work_id": _derive_work_id(frontmatter),
        "manifestation_id": sol_id,
        "sol_id": sol_id,
        "book_id": frontmatter["book_id"],
        "title": frontmatter.get("title", ""),
        "title_en": frontmatter.get("title_en", ""),
        "author": frontmatter.get("author", ""),
        "author_en": frontmatter.get("author_en", ""),
        "book_type": frontmatter.get("book_type", ""),
        "language": frontmatter.get("language", ""),
        "page_count": frontmatter.get("page_count", 0),
    }


def _extract_pages(raw: dict, book_id: str, source_name: str, config: Config) -> list[Page]:
    content_block = _require(raw, "content", source_name)
    if book_id not in content_block:
        raise IngestError(
            f"{source_name}: content block missing key '{book_id}'. "
            f"Available keys: {list(content_block.keys())}"
        )

    raw_pages = content_block[book_id]
    if not isinstance(raw_pages, list):
        raise IngestError(f"{source_name}: content[{book_id}] must be a list, got {type(raw_pages).__name__}")

    pages: list[Page] = []
    for i, raw_page in enumerate(raw_pages):
        page = _parse_page(raw_page, i, source_name)
        pages.append(page)

    if not pages:
        raise IngestError(f"{source_name}: no pages found in content[{book_id}]")

    _classify_pages(pages, config)
    return pages


def _parse_page(raw_page: dict, index: int, source_name: str) -> Page:
    if not isinstance(raw_page, dict):
        raise IngestError(f"{source_name}: page at index {index} is not a dict")

    page_number = raw_page.get("page_number")
    if not isinstance(page_number, int):
        raise IngestError(
            f"{source_name}: page at index {index} has invalid page_number: {page_number!r}"
        )

    raw_content = raw_page.get("content", "")
    if not isinstance(raw_content, str):
        raise IngestError(
            f"{source_name}: page {page_number} content is not a string: {type(raw_content).__name__}"
        )

    raw_footnote = raw_page.get("footnote")
    if raw_footnote is not None and not isinstance(raw_footnote, str):
        raise IngestError(
            f"{source_name}: page {page_number} footnote is not a string: {type(raw_footnote).__name__}"
        )

    return Page(
        page_number=page_number,
        page_name=raw_page.get("page_name", str(page_number)).strip(),
        text=normalize(raw_content),
        footnote=normalize(raw_footnote) if raw_footnote else None,
    )


def _classify_pages(pages: list[Page], config: Config) -> None:
    """Mark early pages as non-content (frontmatter heuristic).

    Pages before the first page with substantial text are likely
    title pages, copyright pages, or publication information.
    A page is considered content when it has both enough total characters
    AND at least one line exceeding the minimum line length — this prevents
    title pages with many short lines from leaking into content.
    Thresholds are read from config.
    """
    frontmatter_max = int(config.thresholds["frontmatter_page_max"])
    content_min_chars = int(config.thresholds["content_page_min_chars"])
    content_min_line_length = int(config.thresholds["content_min_line_length"])
    content_started = False
    for page in pages:
        if not content_started:
            if _looks_like_content(page.text, content_min_chars, content_min_line_length):
                content_started = True
            elif page.page_number <= frontmatter_max:
                page.is_content = False
        if not page.text:
            page.is_content = False


def _looks_like_content(text: str, min_chars: int, min_line_length: int) -> bool:
    """Check if page text looks like real content (not frontmatter).

    Requires both sufficient total characters and at least one line
    long enough to be running prose. Frontmatter pages often exceed
    the character threshold but consist entirely of short lines
    (title, author, publisher, date — each under ~50 chars).
    """
    if len(text) <= min_chars:
        return False
    return any(len(line) >= min_line_length for line in text.split("\n"))


def _extract_toc(raw: dict, book_id: str) -> list[dict]:
    toc_block = raw.get("toc", {})
    return toc_block.get(book_id, []) if isinstance(toc_block, dict) else []


def _require(d: dict, key: str, source: str) -> object:
    if key not in d:
        raise IngestError(f"{source}: required field '{key}' is missing")
    return d[key]


def _derive_work_id(frontmatter: dict) -> str:
    """Derive a stable work_id from author + title.

    Uses sol_id as the work_id when available and stable.
    Falls back to a slug derived from author + title.
    """
    sol_id = frontmatter.get("sol_id", "")
    if sol_id:
        return sol_id

    author = frontmatter.get("author", "unknown")
    title = frontmatter.get("title", "unknown")
    slug = f"{author}_{title}"[:64]
    return slug.replace(" ", "_")
