"""
Extractor registry for Phase 3.

Maps extractor name strings from config to callable extractor functions.
Extractors consume span.patterns — they do not re-scan span.text.

VALID_ENTITY_TYPES is the single source of truth for entity type validation.
Each extractor module declares the types it produces; the registry unions them.
"""

from __future__ import annotations

from collections.abc import Callable

from src.extractors.biography import (
    BIOGRAPHY_ENTITY_TYPES,
    date_extractor,
    person_extractor,
)
from src.extractors.hadith import HADITH_ENTITY_TYPES, narrator_extractor
from src.models import Entity, Span

EXTRACTOR_REGISTRY: dict[str, Callable[[Span], list[Entity]]] = {
    "narrator_extractor": narrator_extractor,
    "person_extractor": person_extractor,
    "date_extractor": date_extractor,
}

VALID_ENTITY_TYPES: frozenset[str] = HADITH_ENTITY_TYPES | BIOGRAPHY_ENTITY_TYPES
