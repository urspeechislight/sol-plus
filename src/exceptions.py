"""
Shared pipeline exceptions for sol-next.

One file. No per-phase exception hierarchies.
Every exception carries enough context to diagnose without reading source.
"""

from __future__ import annotations


class PipelineError(Exception):
    """Base class for all pipeline errors."""


class IngestError(PipelineError):
    """Raised when Phase 1 cannot load or parse a manuscript JSON."""


class SegmentError(PipelineError):
    """Raised when Phase 2 encounters an unrecoverable structural error.

    FSM invalid state, missing config rule for a span type, etc.
    """


class ExtractError(PipelineError):
    """Raised when Phase 3 cannot extract or atomicize a span."""


class EnrichError(PipelineError):
    """Raised when Phase 4 cannot reach a required external service."""


class GraphError(PipelineError):
    """Raised when Phase 5 cannot write to Neo4j or Qdrant."""


class ConfigError(PipelineError):
    """Raised when config/sol.yaml is missing, malformed, or has broken references."""
