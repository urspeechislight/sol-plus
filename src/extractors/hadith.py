"""
Hadith extractors for Phase 3.

Produce Entity objects from Pattern matches on HADITH_TRANSMISSION spans.
Extractors consume span.patterns — they do not re-scan span.text.
"""

from __future__ import annotations

from src.models import Entity, Span

HADITH_ENTITY_TYPES: frozenset[str] = frozenset({"NARRATOR_NAME"})

PATTERN_MATN_BOUNDARY_HINT = "MATN_BOUNDARY_HINT"
PATTERN_ATTRIBUTION = "ATTRIBUTION"
PATTERN_SPEECH_VERB_GENERIC = "SPEECH_VERB_GENERIC"


def _speech_verb_cap(span: Span) -> int:
    """Find the isnad end when no MATN_BOUNDARY_HINT is present.

    Uses the first SPEECH_VERB_GENERIC after the last ATTRIBUTION as a
    secondary cap. Hadiths without a prophet reference (athar, dialogue
    hadiths) typically transition from isnad to matn at a speech verb
    like قال. Without this cap the last narrator name would extend to
    the end of the span, swallowing the matn text.

    Returns len(span.text) when no usable cap is found.
    """
    attributions = sorted(
        (p for p in span.patterns if p.pattern_id == PATTERN_ATTRIBUTION),
        key=lambda p: p.char_start,
    )
    if not attributions:
        return len(span.text)

    last_attr_end = attributions[-1].char_end
    speech_verbs = sorted(
        (p for p in span.patterns if p.pattern_id == PATTERN_SPEECH_VERB_GENERIC),
        key=lambda p: p.char_start,
    )
    for sv in speech_verbs:
        if sv.char_start >= last_attr_end:
            return sv.char_start
    return len(span.text)


def find_isnad_end(span: Span) -> int:
    """Determine the character position where the isnad ends.

    Combines the speech-verb cap with isnad-zone-constrained MATN_BOUNDARY_HINT
    selection to find the correct isnad/matn boundary.

    The speech-verb cap (first SPEECH_VERB_GENERIC after the last ATTRIBUTION)
    bounds the isnad zone. Only MATN_BOUNDARY_HINT matches that fall within this
    zone are considered — hints deep in the narrative text (e.g. "قال رسول الله"
    inside a story) are ignored.

    Priority:
        1. MATN_BOUNDARY_HINT within [last_attr_end, speech_cap] — most precise
        2. speech_cap (first speech verb after last attribution) — secondary
        3. len(span.text) — no boundary detected

    Args:
        span: A span with .patterns populated by Phase 2.

    Returns:
        Character position where the isnad ends and matn begins.
    """
    speech_cap = _speech_verb_cap(span)

    attributions = sorted(
        (p for p in span.patterns if p.pattern_id == PATTERN_ATTRIBUTION),
        key=lambda p: p.char_start,
    )
    last_attr_end = attributions[-1].char_end if attributions else 0

    matn_hints = sorted(
        (p for p in span.patterns
         if p.pattern_id == PATTERN_MATN_BOUNDARY_HINT
         and p.char_start >= last_attr_end
         and p.char_start <= speech_cap),
        key=lambda p: p.char_start,
    )

    if matn_hints:
        return matn_hints[0].char_start
    return speech_cap


def narrator_extractor(span: Span) -> list[Entity]:
    """Extract narrator name entities from a hadith transmission span.

    Uses ATTRIBUTION pattern positions as delimiters to slice narrator names
    from the isnad text. The text between consecutive transmission verbs is
    the narrator — genealogy markers (بن, ابن) are part of that name and are
    not extracted separately. The isnad boundary (from find_isnad_end) caps
    extraction so matn text is never extracted as a narrator name.

    Algorithm:
        1. Find isnad_end via find_isnad_end() — uses MATN_BOUNDARY_HINT
           constrained to the isnad zone, falling back to the speech-verb cap.
        2. Collect ATTRIBUTION patterns whose char_start < isnad_end, sorted.
        3. For each attribution, strip the text between this verb and the next
           (or isnad_end) and emit a NARRATOR_NAME entity if non-empty.

    Args:
        span: A span with .patterns populated by Phase 2.

    Returns:
        List of NARRATOR_NAME Entity objects, one per narrator in the isnad.
    """
    isnad_end = find_isnad_end(span)

    attribution_patterns = sorted(
        [p for p in span.patterns
         if p.pattern_id == PATTERN_ATTRIBUTION and p.char_start < isnad_end],
        key=lambda p: p.char_start,
    )

    entities: list[Entity] = []
    for i, attr_p in enumerate(attribution_patterns):
        name_start = attr_p.char_end
        name_end = (
            attribution_patterns[i + 1].char_start
            if i + 1 < len(attribution_patterns)
            else isnad_end
        )
        name_slice = span.text[name_start:name_end]
        name_text = name_slice.strip()
        if name_text:
            leading = len(name_slice) - len(name_slice.lstrip())
            entities.append(Entity(
                entity_id="",
                entity_type="NARRATOR_NAME",
                text=name_text,
                char_start=name_start + leading,
                char_end=name_start + leading + len(name_text),
            ))

    return entities
