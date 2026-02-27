"""
Phase 4: ENRICH

Input:  Manuscript (units populated via Phase 3)
Output: Manuscript (units with .text_en and .embedding populated)

For each unit in manuscript.units:
  1. Check Redis cache (key: sha256(text_ar + profile_id)). Cache hit → skip LLM.
  2. If cache miss: call LLM with the translation profile for unit.behavior.
  3. Generate embedding for unit.text_ar (always — Arabic indexing is unconditional).
  4. If translation succeeded: generate embedding for unit.text_en.

Translation failure is recoverable: log the failure, leave unit.text_en=None.
The unit is still embedded in Arabic and still indexed.

Embedding failure is not recoverable: raise EnrichError.
A unit with no embedding cannot be retrieved semantically.

See src/phases/CLAUDE.md for the full intent and failure contract.
"""

from __future__ import annotations

from src.models import Manuscript
from src.utils.config import Config


async def enrich(manuscript: Manuscript, config: Config) -> Manuscript:
    """Translate and embed all units in the manuscript.

    Populates unit.text_en and unit.embedding for every unit.
    Units that fail translation get text_en=None; a warning is logged per failure.

    Raises EnrichError if Redis or the embedding model is unreachable.
    """
    raise NotImplementedError
