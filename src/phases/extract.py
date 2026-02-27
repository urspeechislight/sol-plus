"""
Phase 3: EXTRACT

Input:  Manuscript (spans with .behavior and .hierarchy set by Phase 2)
Output: Manuscript (spans with .entities and .units set)

For each span, Phase 3 does two things gated by span.behavior:

1. ENTITY EXTRACTION
   Look up config.extractors[span.behavior]. Run each listed extractor.
   Extractors consume span.patterns — they do not re-scan span.text.
   Entity IDs are assigned after collection, scoped to the producing span.

2. ATOMICIZATION
   Look up config.atomicizers[span.behavior]. Split the span into atomic units.
   If span.footnote_text is set, one FOOTNOTE_UNIT is appended after the main units.

A content span with no units after atomicization is a bug. Raise ExtractError.

See CLAUDE.md for the full intent and failure contract.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from src.exceptions import ExtractError
from src.extractors import EXTRACTOR_REGISTRY, VALID_ENTITY_TYPES
from src.extractors.hadith import find_isnad_end
from src.models import Entity, HierarchyPath, Manuscript, Span, Unit
from src.utils.config import Config

logger = logging.getLogger(__name__)

EXTRACT__UNIT_TYPE__FOOTNOTE = "FOOTNOTE_UNIT"
EXTRACT__UNIT_ID__FORMAT = "{manifestation_id}_u{index:04d}"
EXTRACT__ENTITY_ID__FORMAT = "{span_id}_e{index:02d}"


def _validate_atomicizer_coverage(manuscript: Manuscript, config: Config) -> None:
    for span in manuscript.spans:
        behavior = span.behavior
        if behavior is not None and behavior not in config.atomicizers:
            raise ExtractError(f"No atomicizer rule for behavior: {behavior}")


def _build_extractor_registry(
    config_extractors: dict[str, list[str]],
) -> dict[str, list[Callable[[Span], list[Entity]]]]:
    registry: dict[str, list[Callable[[Span], list[Entity]]]] = {}
    for behavior_id, extractor_names in config_extractors.items():
        callables: list[Callable[[Span], list[Entity]]] = []
        for name in extractor_names:
            if name not in EXTRACTOR_REGISTRY:
                raise ExtractError(f"Unknown extractor: {name}")
            callables.append(EXTRACTOR_REGISTRY[name])
        registry[behavior_id] = callables
    return registry


def _extract_entities(
    span: Span,
    extractor_registry: dict[str, list[Callable[[Span], list[Entity]]]],
    behavior: str,
) -> list[Entity]:
    entities: list[Entity] = []
    for extractor_fn in extractor_registry.get(behavior, []):
        entities.extend(extractor_fn(span))
    return entities


def _atomicize_whole_span(
    span: Span,
    config_rule: dict[str, Any],
    start_index: int,
    manifestation_id: str,
    behavior: str,
    hierarchy: HierarchyPath,
) -> list[Unit]:
    unit_type: str = config_rule["unit_type"]
    unit_id = EXTRACT__UNIT_ID__FORMAT.format(
        manifestation_id=manifestation_id, index=start_index
    )
    return [Unit(
        unit_id=unit_id,
        text_ar=span.text,
        unit_type=unit_type,
        behavior=behavior,
        span_id=span.span_id,
        page_start=span.page_start,
        page_end=span.page_end,
        hierarchy=hierarchy,
    )]


def _atomicize_sanad_matn(
    span: Span,
    config_rule: dict[str, Any],
    start_index: int,
    manifestation_id: str,
    behavior: str,
    hierarchy: HierarchyPath,
) -> list[Unit]:
    isnad_end = find_isnad_end(span)
    if isnad_end < len(span.text):
        isnad_text = span.text[:isnad_end].strip()
        matn_text = span.text[isnad_end:].strip()
        if isnad_text and matn_text:
            unit_types: dict[str, Any] = config_rule["unit_types"]
            isnad_id = EXTRACT__UNIT_ID__FORMAT.format(
                manifestation_id=manifestation_id, index=start_index
            )
            matn_id = EXTRACT__UNIT_ID__FORMAT.format(
                manifestation_id=manifestation_id, index=start_index + 1
            )
            return [
                Unit(
                    unit_id=isnad_id,
                    text_ar=isnad_text,
                    unit_type=unit_types["isnad"],
                    behavior=behavior,
                    span_id=span.span_id,
                    page_start=span.page_start,
                    page_end=span.page_end,
                    hierarchy=hierarchy,
                ),
                Unit(
                    unit_id=matn_id,
                    text_ar=matn_text,
                    unit_type=unit_types["matn"],
                    behavior=behavior,
                    span_id=span.span_id,
                    page_start=span.page_start,
                    page_end=span.page_end,
                    hierarchy=hierarchy,
                ),
            ]
        logger.warning(
            "Span %s: isnad_end at char %d produced empty split; using fallback",
            span.span_id,
            isnad_end,
        )

    fallback_type: str = config_rule["fallback_unit_type"]
    fallback_id = EXTRACT__UNIT_ID__FORMAT.format(
        manifestation_id=manifestation_id, index=start_index
    )
    return [Unit(
        unit_id=fallback_id,
        text_ar=span.text,
        unit_type=fallback_type,
        behavior=behavior,
        span_id=span.span_id,
        page_start=span.page_start,
        page_end=span.page_end,
        hierarchy=hierarchy,
    )]


def _make_footnote_unit(
    unit_id: str,
    footnote_text: str,
    behavior: str,
    hierarchy: HierarchyPath,
    span: Span,
) -> Unit:
    return Unit(
        unit_id=unit_id,
        text_ar=footnote_text,
        unit_type=EXTRACT__UNIT_TYPE__FOOTNOTE,
        behavior=behavior,
        span_id=span.span_id,
        page_start=span.page_start,
        page_end=span.page_end,
        hierarchy=hierarchy,
    )


_STRATEGY_DISPATCH: dict[str, Callable[..., list[Unit]]] = {
    "whole_span": _atomicize_whole_span,
    "sanad_matn_split": _atomicize_sanad_matn,
}


def extract(manuscript: Manuscript, config: Config) -> Manuscript:
    """Extract entities and atomicize spans into units.

    Validates atomicizer coverage first. Then for each span, runs the
    registered extractors for its behavior (producing Entity objects from
    already-detected Pattern objects), assigns entity IDs scoped to the
    span, then atomicizes the span into Unit objects using the configured
    strategy. Appends a FOOTNOTE_UNIT when span.footnote_text is set.

    Args:
        manuscript: A Manuscript with spans populated and labeled by Phase 2.
        config: The pipeline configuration with atomicizers and extractors.

    Returns:
        The same Manuscript with span.entities and span.units populated.

    Raises:
        ExtractError: If a behavior lacks an atomicizer rule, a strategy is
            unknown, an extractor name is unrecognized, an entity type is
            invalid, or a content span produces zero units.
    """
    _validate_atomicizer_coverage(manuscript, config)
    extractor_registry = _build_extractor_registry(config.extractors)
    unit_counter = 0

    for span in manuscript.spans:
        if span.behavior is None:
            raise ExtractError(f"Span {span.span_id} has no behavior from Phase 2")
        if span.hierarchy is None:
            raise ExtractError(f"Span {span.span_id} has no hierarchy from Phase 2")

        behavior: str = span.behavior
        hierarchy: HierarchyPath = span.hierarchy

        entities = _extract_entities(span, extractor_registry, behavior)
        for idx, entity in enumerate(entities):
            if entity.entity_type not in VALID_ENTITY_TYPES:
                raise ExtractError(f"Unknown entity_type: {entity.entity_type}")
            entity.entity_id = EXTRACT__ENTITY_ID__FORMAT.format(
                span_id=span.span_id, index=idx
            )

        config_rule: dict[str, Any] = config.atomicizers[behavior]
        strategy: str = config_rule["strategy"]
        if strategy not in _STRATEGY_DISPATCH:
            raise ExtractError(f"Unknown atomicizer strategy: {strategy}")

        atomicizer_fn = _STRATEGY_DISPATCH[strategy]
        units = atomicizer_fn(
            span, config_rule, unit_counter, manuscript.manifestation_id, behavior, hierarchy
        )
        unit_counter += len(units)

        if not units and span.text.strip():
            raise ExtractError(f"Span {span.span_id} produced zero units")

        if span.footnote_text:
            fn_unit_id = EXTRACT__UNIT_ID__FORMAT.format(
                manifestation_id=manuscript.manifestation_id, index=unit_counter
            )
            units.append(_make_footnote_unit(fn_unit_id, span.footnote_text, behavior, hierarchy, span))
            unit_counter += 1

        span.entities = entities
        span.units = units

    logger.info(
        "Extracted from %s: %d spans, %d units, %d entities",
        manuscript.manifestation_id,
        len(manuscript.spans),
        len(manuscript.units),
        len(manuscript.entities),
    )
    return manuscript
