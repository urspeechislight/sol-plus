"""
Phase 5: GRAPH

Input:  Manuscript (spans, units, entities all populated)
Output: Edges written to Neo4j; embeddings upserted to Qdrant

Three sub-phases, run in order:

1. STRUCTURAL EDGES (deterministic, confidence=1.0)
   From hierarchy: CONTAINS, NEXT, HAS_SECTION.
   From span annotations: HAS_FOOTNOTE.
   From entities: CITATION_MENTION, MENTIONS_PERSON, HAS_SANAD, HAS_MATN, NARRATES_FROM.
   No inference. If inputs are correct, edges are correct.

2. CITATION RESOLUTION
   CITATION_MENTION → candidate search in Qdrant → dominance check →
   CITES edge if confidence >= config.thresholds["citation_confidence_min"].
   Ambiguous → leave as CITATION_MENTION with candidates logged.
   Unresolvable → log; do not invent a target.

3. CROSS-EDITION ALIGNMENT (when multiple manifestations present)
   Select canonical manifestation. Align non-canonical units via:
     fingerprint match → hierarchy+page match → semantic similarity fallback.
   Emit ALIGNS_WITH edges. Promote work-level edges.

Partial writes are not acceptable. Either a batch completes or it raises.

See src/phases/CLAUDE.md for the full intent and failure contract.
"""

from __future__ import annotations

from src.models import Edge, Manuscript
from src.utils.config import Config


def graph(manuscript: Manuscript, config: Config) -> list[Edge]:
    """Build all knowledge graph edges and write to storage.

    Returns the complete list of edges produced.

    Raises GraphError if Neo4j or Qdrant is unreachable, if an edge references
    a node that does not exist, or if a write batch fails.
    """
    raise NotImplementedError
