"""
Core data types for sol-next.

One Span type. One Unit type. One Edge type. No wrappers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Pattern:
    """A surface-level pattern detected in a span's text.

    Replaces SubSpan from SOL. Same concept, simpler name.
    Detected by Phase 2 (segment) using regex rules from config/sol.yaml.
    """

    pattern_id: str
    matched_text: str
    char_start: int
    char_end: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class HierarchyPath:
    """The document position of a span — which book, chapter, section it belongs to.

    Assigned by Phase 2 (segment) via the six FSM trackers.
    """

    path: list[str]
    path_ids: list[str]
    depth: int


@dataclass
class Entity:
    """An extracted entity from a span.

    Assigned by Phase 3 (extract) via behavior-gated extractors.
    """

    entity_id: str
    entity_type: str
    text: str
    char_start: int
    char_end: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Unit:
    """A minimal atomic semantic unit — one coherent thought.

    Assigned by Phase 3 (extract) via behavior-specific atomicizers.
    Enriched by Phase 4 (enrich) with translation and embedding.
    """

    unit_id: str
    text_ar: str
    unit_type: str
    behavior: str
    span_id: str
    page_start: int
    page_end: int
    hierarchy: HierarchyPath
    text_en: str | None = None
    embedding: list[float] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Span:
    """A structural segment of a manuscript.

    One type, used from Phase 2 through Phase 5.
    Fields are filled in progressively — behavior in Phase 2,
    entities and units in Phase 3. Never rewrapped.
    """

    span_id: str
    text: str
    page_start: int
    page_end: int
    span_type: str
    patterns: list[Pattern] = field(default_factory=list)
    behavior: str | None = None
    hierarchy: HierarchyPath | None = None
    entities: list[Entity] | None = None
    units: list[Unit] | None = None
    footnote_text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Page:
    """A single page from an ingested manuscript.

    Produced by Phase 1 (ingest). Input to Phase 2 (segment).
    """

    page_number: int
    page_name: str
    text: str
    footnote: str | None = None
    is_content: bool = True


@dataclass
class Edge:
    """A knowledge graph edge.

    Produced by Phase 5 (graph). Structural edges have confidence=1.0;
    citation and alignment edges carry genuine confidence scores.
    """

    edge_id: str
    edge_type: str
    source_id: str
    target_id: str
    confidence: float
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class Manuscript:
    """The complete processed state of one manuscript.

    Passed between phases. Phases mutate it in-place.
    """

    work_id: str
    manifestation_id: str
    pages: list[Page] = field(default_factory=list)
    spans: list[Span] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def units(self) -> list[Unit]:
        """All atomic units across all spans."""
        return [u for s in self.spans if s.units for u in s.units]

    @property
    def entities(self) -> list[Entity]:
        """All entities across all spans."""
        return [e for s in self.spans if s.entities for e in s.entities]
