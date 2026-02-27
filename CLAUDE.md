# SOL-NEXT

A pipeline that transforms digitized classical Arabic manuscripts into a structured, searchable, multilingual knowledge graph.

---

## The Mission

A scholar reading Ibn Hajar's Fath al-Bari should be able to follow a hadith's isnad back through the narrators, trace its parallel transmissions across other collections, read the Arabic alongside an English translation, and discover which later scholars cited or commented on the same hadith. Today none of that is possible without years of specialized knowledge and manual effort.

This pipeline makes it possible — at scale, across the entire corpus.

---

## The Architecture: Five Phases

```
Phase 1: INGEST    — JSON → normalized pages + metadata
Phase 2: SEGMENT   — pages → labeled spans + hierarchy
Phase 3: EXTRACT   — labeled spans → entities + atomic units
Phase 4: ENRICH    — units → translations + embeddings
Phase 5: GRAPH     — everything → knowledge graph edges
```

Each phase is a function with a declared input type and output type. The domain knowledge — Arabic patterns, behavior routing rules, entity types, graph schema — lives in `config/sol.yaml`. The Python executes it.

The data flow:

```python
manuscript = ingest("book.json")       # Phase 1
manuscript = segment(manuscript, cfg)  # Phase 2
manuscript = extract(manuscript, cfg)  # Phase 3
manuscript = enrich(manuscript, cfg)   # Phase 4
manuscript = graph(manuscript, cfg)    # Phase 5
```

Five function calls. The entire pipeline.

---

## Data Integrity Is Non-Negotiable

This is production software processing irreplaceable scholarly data. These rules are absolute and override any other instinct toward convenience.

**Fail loudly. Always.**
If a phase cannot produce correct output, it raises. It does not return a plausible-looking wrong answer. A span that cannot be labeled is not silently labeled `UNKNOWN` and passed forward. An unresolvable config reference is not silently skipped. Fake output corrupts the knowledge graph in ways that are invisible until a scholar trusts a wrong connection.

**No `except: pass`. No `except Exception: continue`.**
Every exception must be logged with full context (`logger.error(..., exc_info=True)`) and either re-raised or converted to a typed error with `raise NewError(...) from e`. Silent suppression of exceptions is forbidden without exception.

**No silent fallbacks.**
If you find yourself writing `or []`, `or {}`, `or "UNKNOWN"` in an error path, stop and ask: is this a legitimate empty state, or am I hiding a bug? If you are hiding a bug, raise instead.

**Explicit degraded modes only.**
Every span must receive an explicit behavior label. `GENERAL_PROSE` is the correct fallback when a span has no detectable pattern — segment.py always assigns it when no more specific rule matches. `span.behavior = None` passed silently to the next phase is never valid.

**Structural errors are bugs, not edge cases.**
If Phase 2 encounters an FSM state it does not expect, that is a bug in the config or the input — not a recoverable situation. Raise. Log the FSM state and the triggering span. Fix the source. Do not patch the symptom with a default.

**Wrong is worse than absent.**
The pipeline produces claims about manuscripts. A wrong claim — a wrong behavior label, a spurious citation edge, an incorrect narrator extraction — is actively harmful. When uncertain, produce nothing and log why. Uncertainty is not a reason to invent.

---

## Core Design Principles

**One Span type.**
A `Span` is a `Span` from Phase 2 through Phase 5. Its fields are populated progressively. It is never rewrapped in a new type to signal that more data has been added. If you need to know whether a span has been through Phase 3, check `span.units is not None`.

**Patterns route directly to behaviors.**
There is no intermediate signal representation. Arabic text patterns detected in a span are looked up in the behavior routing table. The routing table returns a behavior label. Combinatorial rules (two patterns co-occurring → one behavior) are expressed in the routing table as multi-pattern requirements, not in Python scoring machinery.

**Config is one file.**
`config/sol.yaml` contains all registries: patterns, behaviors, atomicizers, extractors, graph schema, thresholds. Every cross-reference between registries is within that one file. Validation at startup catches every broken reference before the pipeline runs.

**No thresholds in Python.**
Every numeric threshold lives in `config/sol.yaml` under `thresholds:`. Python code reads `config.thresholds["citation_confidence_min"]`. It never contains a literal `0.85`.

**No per-phase exception classes.**
One `exceptions.py` at `src/exceptions.py`. Phase-specific error context is carried in the exception message and logged fields, not in a class hierarchy.

---

## Directory Layout

```
sol-next/
├── CLAUDE.md                   ← this file
├── RATIONALIZATION.md          ← the architectural analysis
├── pyproject.toml
├── config/
│   └── sol.yaml                ← all registries + thresholds in one file
├── src/
│   ├── models/                 ← Span, Unit, Entity, Edge, Page, Manuscript
│   │   └── __init__.py
│   ├── phases/                 ← one file per phase; each has its own CLAUDE.md
│   │   ├── CLAUDE.md           ← what each phase does and why
│   │   ├── ingest.py
│   │   ├── segment.py
│   │   ├── extract.py
│   │   ├── enrich.py
│   │   └── graph.py
│   ├── db/                     ← storage: postgres, neo4j, qdrant, redis
│   ├── utils/
│   │   ├── config.py           ← load_config(): YAML → Config dataclass
│   │   └── ...                 ← text normalization, hashing, Arabic helpers
│   └── cli/
│       └── main.py             ← entry point: sol run book.json
├── scripts/                    ← one-off tools; not part of the pipeline
├── docs/                       ← architecture decisions, schema documentation
├── tests/
│   ├── phases/                 ← unit tests per phase
│   └── integration/            ← end-to-end tests on sample books
└── data/
    └── samples/                ← sample book JSON files for testing
```

---

## The Core Types

Defined in `src/models/__init__.py`. Read that file before writing any phase code.

- `Manuscript` — the container passed between phases. Phases mutate it in-place.
- `Page` — one page of input text with optional footnote. Produced by Phase 1.
- `Span` — a structural text segment. Populated progressively by Phases 2–3.
- `Unit` — one atomic semantic unit. Populated by Phase 3, enriched by Phase 4.
- `Entity` — one extracted entity from a span. Populated by Phase 3.
- `Edge` — one knowledge graph edge. Produced by Phase 5.
- `Pattern` — a detected surface-level text pattern. Detected by Phase 2.
- `HierarchyPath` — a span's position in the document structure. Assigned by Phase 2.

---

## Config Structure

```yaml
# config/sol.yaml

patterns:       # surface-level text detectors
  - id: ATTRIBUTION
    regex: "حدثنا|أخبرنا|روى عن"

behaviors:      # routing table: pattern combinations → behavior labels
  - id: HADITH_TRANSMISSION
    any_of: [ATTRIBUTION, MATN_BOUNDARY_HINT]
    priority: 80

atomicizers:    # how to split each behavior into units
  HADITH_TRANSMISSION:
    strategy: sanad_matn_split

extractors:     # which extractors to run per behavior
  HADITH_TRANSMISSION: [narrator_extractor]
  BIOGRAPHY:    [person_extractor, date_extractor]

graph:          # edge types and construction rules
  structural:   [CONTAINS, NEXT, HAS_SECTION]
  reference:    [CITATION_MENTION, CITES]

thresholds:     # all numeric thresholds — never hard-code these in Python
  citation_confidence_min: 0.85
  alignment_confidence_min: 0.90
```

---

## Development Workflow

```bash
python -m src.cli.main run data/samples/book.json   # run pipeline
python -m src.cli.main validate-config               # validate config/sol.yaml
pytest tests/                                        # run tests
```
