"""
sol-next pipeline demo server.

Runs Phase 1 (INGEST) and Phase 2 (SEGMENT) on a real book JSON at startup,
then serves the results as a live HTML dashboard with a book browser sidebar.

Usage (from sol-next root):
    python scripts/serve.py /path/to/book.json
    SOL_DEMO_BOOK=/path/to/book.json python scripts/serve.py
"""

from __future__ import annotations

import html
import logging
import os
import re
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

import uvicorn
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.phases.extract import extract
from src.phases.ingest import ingest
from src.phases.segment import segment
from src.utils.config import load_config

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "sol.yaml"
_TEMPLATES_DIR = Path(__file__).parent / "templates"
_BOOKS_DIR = Path(__file__).parent.parent / "data" / "books"

DEMO__SERVER__PORT = 8765
DEMO__PAGE__LIMIT = 20

DEMO__UNIT_SHOW_TEXT_TYPES: frozenset[str] = frozenset({"ISNAD_UNIT", "MATN_UNIT", "FOOTNOTE_UNIT"})

_FOOTNOTE_MARKER_RE = re.compile(r"\((\d+)\)")

# Arabic honorific ligatures that render oversized in Scheherazade New.
# U+FD40–FD4F + U+FDFA–FDFB (ﷺ ﷻ) — wrap in <span class="hon"> to scale down.
_HONORIFIC_RE = re.compile(r"[\uFD40-\uFD4F\uFDFA\uFDFB]")


def _superscript_markers(text: str, valid_numbers: set[str] | None = None) -> str:
    """HTML-escape text and convert (N) footnote markers to superscript tags.

    Only markers whose number appears in valid_numbers are converted.
    If valid_numbers is None, all (N) markers are converted.
    Also wraps Arabic honorific ligatures in a scaling span.
    """
    escaped = html.escape(text)

    def _replace(m: re.Match[str]) -> str:
        num = m.group(1)
        if valid_numbers is not None and num not in valid_numbers:
            return m.group(0)
        return f'<sup class="fn-marker">{num}</sup>'

    result = _FOOTNOTE_MARKER_RE.sub(_replace, escaped)
    result = _HONORIFIC_RE.sub(lambda m: f'<span class="hon">{m.group(0)}</span>', result)
    return result

_templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
_pipeline_data: dict[str, Any] = {}
_books_tree: list[dict[str, Any]] = []


def _resolve_book_path() -> Path:
    """Resolve the book path from CLI argument or environment variable.

    Priority: CLI argument > SOL_DEMO_BOOK env var.

    Returns:
        Resolved path to the book JSON file.

    Raises:
        SystemExit: If no path is provided or the file does not exist.
    """
    raw = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SOL_DEMO_BOOK", "")
    if not raw:
        logger.error("No book path provided.")
        logger.error("Usage: python scripts/serve.py /path/to/book.json")
        logger.error("       SOL_DEMO_BOOK=/path/to/book.json python scripts/serve.py")
        sys.exit(1)
    path = Path(raw)
    if not path.exists():
        logger.error("Book not found: %s", path)
        sys.exit(1)
    return path


def _scan_books() -> list[dict[str, Any]]:
    """Scan data/books/ and build a genre→books tree for the sidebar.

    Each genre entry contains its name and a list of books with their
    filesystem path and display title extracted from the filename.

    Returns:
        List of genre dicts, each with 'genre' and 'books' keys.
    """
    if not _BOOKS_DIR.exists():
        return []
    tree: list[dict[str, Any]] = []
    for genre_dir in sorted(_BOOKS_DIR.iterdir()):
        if not genre_dir.is_dir() or genre_dir.name.startswith("."):
            continue
        books: list[dict[str, str]] = []
        for book_file in sorted(genre_dir.glob("*.json")):
            name = book_file.stem
            display = name.replace("_", " ").rsplit(" Arabic ", 1)[0].rsplit(" Persian ", 1)[0]
            slug = f"{genre_dir.name}/{book_file.name}"
            books.append({
                "path": str(book_file),
                "filename": book_file.name,
                "display": display,
                "slug": slug,
            })
        if books:
            genre_label = genre_dir.name.replace("-", " ").title()
            tree.append({"genre": genre_label, "genre_id": genre_dir.name, "books": books})
    return tree


def _run_pipeline(book_path: Path) -> dict[str, Any]:
    """Execute Phases 1, 2, and 3 on the given manuscript.

    Limits processing to the first DEMO__PAGE__LIMIT pages so the dashboard
    remains readable regardless of book size.

    Args:
        book_path: Path to a real book JSON file.

    Returns:
        A flat dict of pipeline results passed directly to the dashboard template.
    """
    config = load_config(_CONFIG_PATH)
    manuscript = ingest(book_path, config)
    manuscript.pages = manuscript.pages[:DEMO__PAGE__LIMIT]
    manuscript = segment(manuscript, config)
    manuscript = extract(manuscript, config)

    content_pages = sum(1 for p in manuscript.pages if p.is_content)

    behavior_counts: dict[str, int] = {}
    unit_type_counts: dict[str, int] = {}
    entity_type_counts: dict[str, int] = {}

    spans_data: list[dict[str, Any]] = []
    for span in manuscript.spans:
        behavior = span.behavior
        behavior_counts[behavior] = behavior_counts.get(behavior, 0) + 1

        fn_numbers: set[str] | None = None
        if span.footnote_text:
            fn_numbers = set(_FOOTNOTE_MARKER_RE.findall(span.footnote_text))

        units_data: list[dict[str, Any]] = []
        for u in (span.units or []):
            unit_type_counts[u.unit_type] = unit_type_counts.get(u.unit_type, 0) + 1
            units_data.append({
                "unit_type": u.unit_type,
                "text_ar": u.text_ar if u.unit_type in DEMO__UNIT_SHOW_TEXT_TYPES else "",
            })

        entities_data: list[dict[str, Any]] = []
        for e in (span.entities or []):
            entity_type_counts[e.entity_type] = entity_type_counts.get(e.entity_type, 0) + 1
            entities_data.append({"entity_type": e.entity_type, "text": e.text})

        spans_data.append({
            "span_id": span.span_id,
            "behavior": behavior,
            "page": span.page_start,
            "page_end": span.page_end,
            "hierarchy_path": " › ".join(span.hierarchy.path) if span.hierarchy else "",
            "pattern_ids": sorted({p.pattern_id for p in span.patterns}),
            "text_preview": span.text,
            "text_html": _superscript_markers(span.text, fn_numbers),
            "char_count": len(span.text),
            "footnote": span.footnote_text,
            "footnote_html": _superscript_markers(span.footnote_text, None) if span.footnote_text else None,
            "units": units_data,
            "entities": entities_data,
        })

    behavior_summary = [
        {"behavior": b, "count": c}
        for b, c in sorted(behavior_counts.items(), key=lambda x: -x[1])
    ]
    unit_type_summary = [
        {"unit_type": t, "count": c}
        for t, c in sorted(unit_type_counts.items(), key=lambda x: -x[1])
    ]
    entity_type_summary = [
        {"entity_type": t, "count": c}
        for t, c in sorted(entity_type_counts.items(), key=lambda x: -x[1])
    ]

    pages_data: list[dict[str, Any]] = []
    for page in manuscript.pages:
        fn_numbers: set[str] | None = None
        if page.footnote:
            fn_numbers = set(_FOOTNOTE_MARKER_RE.findall(page.footnote))
        pages_data.append({
            "page_number": page.page_number,
            "page_name": page.page_name,
            "text": page.text,
            "text_html": _superscript_markers(page.text, fn_numbers),
            "footnote": page.footnote,
            "footnote_html": _superscript_markers(page.footnote, None) if page.footnote else None,
            "is_content": page.is_content,
        })

    # Compute a URL-friendly slug (relative to books dir) for bookmarkable URLs.
    try:
        book_slug = str(book_path.resolve().relative_to(_BOOKS_DIR.resolve()))
    except ValueError:
        book_slug = book_path.name

    return {
        "work_id": manuscript.work_id,
        "manifestation_id": manuscript.manifestation_id,
        "title": manuscript.metadata.get("title", ""),
        "author": manuscript.metadata.get("author", ""),
        "book_path": str(book_path),
        "book_slug": book_slug,
        "page_limit": DEMO__PAGE__LIMIT,
        "total_pages": len(manuscript.pages),
        "content_pages": content_pages,
        "skipped_pages": len(manuscript.pages) - content_pages,
        "total_spans": len(manuscript.spans),
        "behavior_count": len(behavior_counts),
        "behavior_summary": behavior_summary,
        "total_units": len(manuscript.units),
        "total_entities": len(manuscript.entities),
        "unit_type_summary": unit_type_summary,
        "entity_type_summary": entity_type_summary,
        "spans": spans_data,
        "pages": pages_data,
    }


@asynccontextmanager
async def _lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    """Resolve book path, scan books directory, run pipeline, yield."""
    global _pipeline_data, _books_tree
    _books_tree = _scan_books()
    book_path = _resolve_book_path()
    logger.info("Loading book: %s", book_path)
    _pipeline_data = _run_pipeline(book_path)
    yield


app = FastAPI(title="sol-next demo", lifespan=_lifespan)


def _resolve_slug(slug: str) -> Path | None:
    """Resolve a book slug (e.g. 'hadith/Sahih_Muslim.json') to an absolute path."""
    candidate = _BOOKS_DIR / slug
    if candidate.exists():
        return candidate
    return None


def _ensure_book_loaded(slug: str | None) -> None:
    """If slug is provided and differs from current book, load it."""
    global _pipeline_data
    if not slug:
        return
    if _pipeline_data.get("book_slug") == slug:
        return
    book_path = _resolve_slug(slug)
    if not book_path:
        logger.error("Book not found for slug: %s", slug)
        return
    logger.info("Loading book: %s", book_path)
    _pipeline_data = _run_pipeline(book_path)


def _render(request: Request, view: str) -> HTMLResponse:
    """Common renderer for dashboard and reader views."""
    return _templates.TemplateResponse(
        name="dashboard.html",
        context={
            "request": request,
            "books_tree": _books_tree,
            "total_books": sum(len(g["books"]) for g in _books_tree),
            "view": view,
            **_pipeline_data,
        },
    )


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, book: str | None = Query(None)) -> HTMLResponse:
    """Render the pipeline results dashboard."""
    _ensure_book_loaded(book)
    return _render(request, "dashboard")


@app.get("/reader", response_class=HTMLResponse)
async def reader(request: Request, book: str | None = Query(None)) -> HTMLResponse:
    """Render the manuscript reader view."""
    _ensure_book_loaded(book)
    return _render(request, "reader")


@app.get("/load")
async def load_book(path: str = Query(..., description="Absolute path to book JSON")) -> RedirectResponse:
    """Load a new book through the pipeline and redirect to dashboard with book in URL."""
    global _pipeline_data
    book_path = Path(path)
    if not book_path.exists():
        logger.error("Book not found: %s", book_path)
        return RedirectResponse(url="/", status_code=302)
    logger.info("Loading book: %s", book_path)
    _pipeline_data = _run_pipeline(book_path)
    return RedirectResponse(url=f"/?book={quote(_pipeline_data['book_slug'])}", status_code=302)


@app.get("/api/data")
async def api_data() -> dict[str, Any]:
    """Return raw pipeline results as JSON."""
    return _pipeline_data


@app.get("/api/books")
async def api_books() -> list[dict[str, Any]]:
    """Return the books directory tree."""
    return _books_tree


if __name__ == "__main__":
    uvicorn.run("scripts.serve:app", host="0.0.0.0", port=DEMO__SERVER__PORT, reload=True)
