"""
Biography extractors for Phase 3.

Produce Entity objects from Pattern matches on BIOGRAPHY spans.
Extractors consume span.patterns — they do not re-scan span.text.
"""

from __future__ import annotations

from src.models import Entity, Span

BIOGRAPHY_ENTITY_TYPES: frozenset[str] = frozenset({"PERSON_TITLE", "DEATH_MARKER", "BIRTH_MARKER"})


def person_extractor(span: Span) -> list[Entity]:
    """Extract person entities from a biography span.

    Produces PERSON_TITLE entities from PERSON_REF patterns detected
    by Phase 2. Returns an empty list if no relevant patterns are found.

    Args:
        span: A span with .patterns populated by Phase 2.

    Returns:
        List of Entity objects extracted from PERSON_REF pattern matches.
    """
    entities: list[Entity] = []
    for pattern in span.patterns:
        if pattern.pattern_id == "PERSON_REF":
            entities.append(Entity(
                entity_id="",
                entity_type="PERSON_TITLE",
                text=pattern.matched_text,
                char_start=pattern.char_start,
                char_end=pattern.char_end,
            ))
    return entities


_DATE_PATTERN_IDS: frozenset[str] = frozenset({"DEATH_MARKER", "BIRTH_MARKER"})


def date_extractor(span: Span) -> list[Entity]:
    """Extract date entities from a biography span.

    Produces entities from DEATH_MARKER and BIRTH_MARKER patterns detected
    by Phase 2. The pattern_id maps directly to the entity_type.
    Returns an empty list if no relevant patterns are found.

    Args:
        span: A span with .patterns populated by Phase 2.

    Returns:
        List of Entity objects extracted from date-related pattern matches.
    """
    return [
        Entity(
            entity_id="",
            entity_type=pattern.pattern_id,
            text=pattern.matched_text,
            char_start=pattern.char_start,
            char_end=pattern.char_end,
        )
        for pattern in span.patterns
        if pattern.pattern_id in _DATE_PATTERN_IDS
    ]
