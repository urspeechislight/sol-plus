# SOL: An Honest Reckoning

This document asks whether the current architecture is the right architecture, or whether it is the result of fourteen months of incremental accretion that built complexity around complexity.

---

## The Weight of What Exists

```
src/steps/    — 47,156 lines across 205 files (17 step directories)
src/settings/ — 6,068 lines across 44 constants files
config/       — 16,299 lines across 69 YAML files

Total         — ~106,000 Python + ~16,000 YAML = ~122,000 lines
```

For context: SQLite's core is ~155,000 lines. We have built something that weighs half of SQLite to process structured JSON files into a database.

The core problem SOL solves:

> Given a JSON file of page-content pairs from a digitized Arabic manuscript, produce a structured, searchable, multilingual knowledge graph.

That problem does not require 122,000 lines. The question is: how did we get here, and what do we actually need?

---

## The Honest Accounting — Where Lines Went and Why

### 1. The SubSpan → Signal → Behavior Chain: 13,128 lines for a routing table

The biggest single source of complexity is the three-step chain:

```
Step 3: Detect SubSpans (patterns in text)          — 4,712 lines
Step 4: Convert SubSpans → Signals                  — 4,544 lines
Step 5: Convert Signals → Behaviors                 — 3,872 lines
                                              Total: 13,128 lines
```

What this chain actually does: given a set of detected patterns in a text segment, decide what kind of content it is (hadith, biography, fiqh ruling, etc.).

That is a routing table. The pattern → behavior mapping is a lookup. The intermediate "Signal" representation — with its own type hierarchy (`SpanSignal`, `SignalSet`, `MutableSpanWithSignals`), Bayesian confidence scoring, cascading suppression logic, ranking engine, and inheritance system — adds ~4,500 lines to represent something that is an intermediate step toward a destination (behavior) that we always knew was the destination.

The Signal layer accumulated because it solved a real problem: some patterns alone are ambiguous and need combining (QURAN_REF + FORMULAIC → divine attribution; MATN_BOUNDARY_HINT alone → prophetic attribution). That combinatorial logic is real and necessary. But it does not require a 4,544-line step. It requires a declarative rule table.

**The question**: could the pattern detection (Step 3's SubSpans) directly produce behavior labels via a lookup table, with a small combinator layer for the multi-pattern cases?

Yes. The routing table in `behavior_routing_table.yaml` already expresses the mapping. The Python machinery in Step 4 executes it. The machinery is the complexity, not the logic.

---

### 2. The Span Wrapper Progression: 5 Types for 1 Concept

```python
StructuralSpan        # Step 3 output
  → SpanWithSignals   # Step 4 output
    → SpanWithBehaviors  # Step 5 output
      → SpanWithHierarchy  # Step 6 output
        → SpanWithExtractions  # Step 7 output
```

Five frozen dataclasses representing what is conceptually one thing: a text segment with accumulated annotations. Each wrapper required its own:
- Type definition
- Serialization code
- Display logic in the walker
- Validation logic
- Export logic in Step 8

The wrapper pattern was motivated by the correct instinct that "each step should know exactly what it produced." But frozen wrappers that unwrap and rewrap at each step are not the right implementation of that instinct. They are a ceremony that costs thousands of lines for zero runtime benefit.

A single `Span` dataclass with optional fields that get filled in — `signals`, `behavior`, `hierarchy_path`, `extractions` — expresses the same information. The type system enforces nothing useful that the tests don't already enforce; `SpanWithSignals` just means "a span that also has `.signal_set`."

---

### 3. The Config Compiler (Step 0): 3,344 Lines to Load YAML

Step 0 compiles 69 YAML files into a frozen `ConfigSnapshot` dataclass. The compilation:
- Validates every cross-reference between registries
- Builds indexes for fast lookup
- Produces a content hash for caching
- Detects schema violations

This is real work. But 3,344 lines for it? The compiler (`compiler.py`: 433 lines), the registry client (`registry_client.py`: 482 lines), the schema validation (`schema_validation.py`: 398 lines), the crossref validation (`crossref_validation.py`: 382 lines), the models (`models.py`: 468 lines) — together they form a mini-application whose sole job is to read config files.

The result (`ConfigSnapshot`) is a frozen dataclass that every downstream step receives. But `ConfigSnapshot` is not the same as its source YAML — it is a compiled representation that some steps use extensively (Step 5's routing table lookup) and others barely touch.

**The question**: if the YAML registries were simpler and fewer (1 behavior registry, 1 pattern registry, 1 entity registry), how much of the compiler would be necessary?

---

### 4. The 44 Constants Files: 6,068 Lines of Named Numbers

The coding standards rule "no magic numbers" was applied so aggressively that:

```python
CLI__WALKER__DEFAULT_END_STEP__INDEX = 10
STEP4__SIGNAL__BIOGRAPHICAL__WINDOW__SIZE__SPANS = 3
UNIT__NON_ATOMICIZABLE__SAMPLE__COUNT__MAX = 5
```

Many constants are used in exactly one place. The naming convention (`DOMAIN__CONTEXT__THING__UNIT`) produces readable names but requires constant archaeology to find the right constant for a given purpose. The 44-file structure means that adding a new threshold requires: decide which file it belongs in, name it correctly, import it correctly, use it. A simple YAML config section for thresholds would serve the same purpose with far less overhead.

---

### 5. Step 8 (Export) and Step 10 (Manifest): 4,250 Lines for I/O

Steps 8 and 10 together produce 4,250 lines of code whose function is: write the pipeline's state to disk in a structured format.

Step 8 (`export_writers.py`, `export_serializers.py`, `export_validation.py`, `export_post_validation.py`, `export_statistics.py`) — 2,362 lines.
Step 10 (`compiler_engine.py`, `work_manifest_generator.py`, `run_api.py`, `ingest_api.py`, `artifacts_api.py`) — 1,888 lines.

These are infrastructure steps. They do not transform the data — they persist it. The persistence infrastructure grew because the Layer A / Layer B boundary is a real distributed systems boundary. But for development and single-book processing, it is overhead.

---

## What Is Actually Necessary

Stripping away the accretion, the essential transformations are:

```
1. INGEST     — JSON pages → clean normalized text + metadata
2. SEGMENT    — text → spans with behavior labels + hierarchy positions
3. EXTRACT    — labeled spans → entities + atomic units
4. ENRICH     — Arabic units → English translations + embeddings
5. GRAPH      — everything → knowledge graph edges
```

Five transformations. Not seventeen.

The complexity in Steps 3–5 (SubSpan → Signal → Behavior) collapses into SEGMENT: detect patterns, consult routing table, assign label. The complexity in Steps 6–7 collapses into SEGMENT (hierarchy) and EXTRACT (entities). The complexity in Steps 11–13 collapses into ENRICH. The complexity in Steps 14–16 collapses into GRAPH.

**What cannot be simplified:**
- The Arabic regex patterns themselves (Step 3) — the linguistic complexity is real
- The behavior routing table — the domain knowledge is real
- The genre-specific atomicizers — a hadith genuinely splits differently from a biography
- The translation + embedding execution — the LLM complexity is real
- The citation resolution algorithm — multi-channel evidence scoring is real
- The cross-edition alignment — the problem is genuinely hard

**What can be simplified:**
- The intermediate representation layers (span wrappers)
- The config compilation infrastructure
- The signal type as distinct from behavior
- The constants file proliferation
- The step boundary ceremony (validation, models, exceptions per step)

---

## The Proposed Architecture for sol-next

### Core Principle: One Span Type, Five Phases, One Config File

```python
@dataclass
class Span:
    span_id: str
    text: str                          # normalized Arabic
    page_start: int
    page_end: int
    span_type: str                     # PARAGRAPH, HEADING, FOOTNOTE, etc.
    patterns: list[Pattern]            # what Step 3 found (was: SubSpans)
    behavior: str | None               # what Phase 2 concluded
    hierarchy: HierarchyPath | None    # where in document structure
    entities: list[Entity] | None      # what Phase 3 extracted
    units: list[Unit] | None           # atomicized units from Phase 3
```

No wrappers. No progression. One type that accumulates annotations in-place.

### Five Phases

```
Phase 1: INGEST    (~400 lines)
  In:  book.json
  Out: list[Page] with normalized text, metadata, TOC, footnotes

Phase 2: SEGMENT   (~1,200 lines)
  In:  list[Page]
  Out: list[Span] with .behavior and .hierarchy filled
  How: regex detection → direct routing table lookup
       hierarchy FSMs run inline during the same pass

Phase 3: EXTRACT   (~600 lines)
  In:  list[Span] with .behavior
  Out: list[Span] with .entities and .units filled
  How: behavior-gated extractor registry + atomicization rules

Phase 4: ENRICH    (~800 lines)
  In:  list[Unit]
  Out: list[Unit] with .translation and .embedding filled
  How: profile-based LLM translation + embedding model
       Redis cache keyed by content hash

Phase 5: GRAPH     (~800 lines)
  In:  list[Span], list[Unit], list[Entity]
  Out: list[Edge] → Neo4j + Qdrant upsert
  How: structural edges (from hierarchy),
       citation edges (from entities, resolved against Qdrant),
       cross-edition edges (from fingerprint/semantic alignment)
```

Estimated total: ~3,800 lines of Python. The domain logic — the patterns, the routing table, the atomicization rules — lives in YAML as it does today. The Python executes it.

### One Config Structure (Not 69 Files)

```yaml
# sol.yaml — the single config file

patterns:          # what to detect in text (was: subspan_type_registry.yaml)
behaviors:         # how to route patterns to labels (was: behavior_routing_table.yaml)
atomicizers:       # how to split each behavior type (was: constants_atomicization.py)
extractors:        # what to extract per behavior (was: extractors_registry.yaml)
graph:             # which edge types to construct (was: span_signal_registry.yaml)
```

Five sections. One file. Cross-references are within one file, not across 69.

### No Intermediate Signal Type

The Signal (Step 4) is an intermediate representation that exists to serve Step 5. If we collapse Steps 4 and 5 into one routing function, Signal disappears. Pattern detection produces evidence; evidence consults the routing table; the routing table returns a behavior. Three lines of logic, not 8,000.

The combinator logic (QURAN_REF + FORMULAIC → divine attribution) lives in the routing table as multi-pattern rules, not in Python scoring machinery.

---

## What sol-next Is Not

It is not a rewrite of the domain knowledge. The Arabic patterns, the behavior taxonomy, the genre-specific processing rules — these are right. They represent real accumulated understanding of the corpus.

It is a rewrite of the **infrastructure around** that domain knowledge. The span wrapper system, the step ceremony, the config compiler, the constants machinery — these are the targets. The YAML registries are the keepers.

---

## The Honest Tradeoff

The current architecture has one genuine advantage: **step isolation**. Each step is independently testable, has a declared contract, and can be debugged in isolation. When Step 4 produces wrong output, you know Step 3 is the cause. This is valuable for a team.

The simplified architecture trades some of that isolation for radical simplicity. The mitigation: each phase still has a declared input/output contract; the contracts are just expressed as function signatures and type annotations, not as separate step directories with their own models, exceptions, and validation modules.

The question is whether the isolation benefit of the current architecture is worth its maintenance cost. Given that the project is currently maintained by one person and the complexity has become a barrier to understanding and improving the system, the answer seems clear.

---

## Immediate Next Steps

```
1. Prototype Phase 2 (SEGMENT) — this is the heart of the question.
   Can direct pattern → behavior routing replace Steps 3+4+5?
   Target: one file, ~400 lines, same outputs on the same test books.

2. If yes: the rest follows naturally.
   Phase 1 is just Steps 1+2 without the wrapper ceremony.
   Phase 3 is just Steps 6+7+9 without the wrapper ceremony.
   Phase 4 is just Steps 11+12+13 in one coordinated function.
   Phase 5 is just Steps 14+15+16 without the per-step infrastructure.

3. Keep the YAML registries from sol.
   They encode domain knowledge that took months to build.
   Simplify their structure; keep their content.

4. Keep the six FSM trackers from Step 6.
   They are genuinely correct and necessary.
   They just do not need to live in a 3,465-line step.
```

---

## The Test

A successful sol-next processes `Ababina_Tatawwur_al-Mushtalih_Arabic_sY-50TSO.json` and produces:
- The same span boundaries as the current Step 3
- The same behavior labels as the current Step 5
- The same extracted entities as the current Step 7
- The same atomic units as the current Step 9

If it does that in fewer than 5,000 lines of Python, sol-next is right. If it takes 10,000 lines, we learned something about which complexity was necessary after all.
