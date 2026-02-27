# Phases

The pipeline is five sequential phases. Each phase receives a `Manuscript`, does one thing to it, and returns it.

| Phase | File | Question it answers |
|---|---|---|
| 1 | `ingest.py` | What is in this JSON file? |
| 2 | `segment.py` | Where are the boundaries, and what is each segment? |
| 3 | `extract.py` | What entities and atomic units does each segment contain? |
| 4 | `enrich.py` | What does each unit say in English, and where does it sit in semantic space? |
| 5 | `graph.py` | What are the connections? |

Each phase has its own CLAUDE.md section below.

---

## Phase 1: INGEST — `ingest.py`

### Intent

Load a manuscript JSON file and produce a clean, structured `Manuscript` object ready for Phase 2.

Phase 1 knows nothing about Arabic patterns or behavior labels. Its only job is to understand the source format: which pages contain content, where footnotes live, what the work identity is, how to normalize the text for downstream processing.

### What it produces

- `manuscript.pages` — ordered list of `Page` objects with normalized text and optional footnotes
- `manuscript.metadata` — work_id, manifestation_id, edition_id, genre, frontmatter facts
- `manuscript.pages` correctly classified: content pages vs. frontmatter vs. backmatter

### What it must not do

- It must not detect patterns or assign behavior labels. That is Phase 2.
- It must not skip pages that look empty. Empty pages are real and must be represented.
- It must not silently drop footnotes. If a page has a footnote field in the JSON, it becomes `page.footnote`.

### Failure modes that must raise, not fallback

- JSON file does not exist or is malformed → raise `IngestError`
- Required fields missing from JSON (work_id, content) → raise `IngestError`
- Page content that cannot be normalized (encoding errors) → raise `IngestError`

---

## Phase 2: SEGMENT — `segment.py`

### Intent

Determine where structural boundaries fall in the manuscript and what each segment means.

This phase makes the behavioral commitment that drives everything downstream. A span leaves Phase 2 with a `behavior` label — a declared answer to "what kind of content is this?" Every subsequent phase acts on that declaration.

Phase 2 has two sub-jobs that happen in a single sequential pass over pages:

**Boundary detection**: Identify where one segment ends and the next begins. Boundaries come from heading patterns, numbered entry markers, paragraph breaks, and structural cues in the text.

**Behavior routing**: For each detected segment, run the pattern detectors from `config.patterns` against the segment text. Then consult `config.behaviors` — a routing table that maps pattern combinations to behavior labels. The routing table decides. There is no intermediate scoring layer.

**Hierarchy tracking**: Six FSM trackers (`KitabBabFasl`, `SurahAyah`, `SanadMatn`, `RijalEntry`, `PoetryQasida`, `ManaqibHikaya`) run in parallel during the same pass. Each span's `.hierarchy` is the combined state of all FSMs at the moment the span is seen.

### What it produces

- `manuscript.spans` — list of `Span` objects, each with `.behavior` and `.hierarchy` set

### What it must not do

- It must not produce spans with `behavior=None` and pass them to Phase 3. Every span must have a behavior or be explicitly marked `UNCLASSIFIED` with a logged reason.
- It must not invent hierarchy paths when the FSM state is ambiguous. Log the ambiguity and raise if the FSM is in an invalid state.
- It must not re-read the database or any external resource. Patterns come from `config`; text comes from `manuscript.pages`.

### Failure modes that must raise, not fallback

- Pattern regex in config is invalid → raise at `load_config()` time, not here
- Routing table has no match and no fallback rule → `UNCLASSIFIED` with warning log
- FSM enters a state not covered by the FSM definition → raise `SegmentError`

---

## Phase 3: EXTRACT — `extract.py`

### Intent

Extract entities and atomicize spans into the minimal semantic units that translation and graph construction will operate on.

Phase 3 knows the behavior of each span (set by Phase 2). It uses that behavior as a gate: only extractors valid for a given behavior are run on a given span. Running the wrong extractor on the wrong behavior produces wrong entities.

Two sub-jobs in one pass:

**Entity extraction**: For each span, look up `config.extractors[span.behavior]` and run the listed extractors. Extractors consume `span.patterns` — they do not re-scan `span.text` with new regex.

**Atomicization**: For each span, look up `config.atomicizers[span.behavior]` and split the span into atomic units. A hadith splits at sanad/matn boundary. A biography entry stays whole. A fiqh ruling splits at hukm clauses. A footnote becomes a single footnote unit whose text is `span.footnote_text`.

### What it produces

- `span.entities` — list of `Entity` objects for each span
- `span.units` — list of `Unit` objects for each span

### What it must not do

- It must not run extractors on spans whose behavior they were not designed for.
- It must not silently produce zero units for a content span. If atomicization produces zero units, that is a bug in the atomicizer or the config — raise `ExtractError`.
- It must not re-detect patterns. `span.patterns` is the input; do not re-run regex.

### Failure modes that must raise, not fallback

- No atomicizer rule for `span.behavior` → raise `ExtractError`
- Atomicizer produces zero units for a non-empty content span → raise `ExtractError`
- Extractor produces an entity with an unknown `entity_type` → raise `ExtractError`

---

## Phase 4: ENRICH — `enrich.py`

### Intent

Translate each Arabic unit into English and generate embeddings for both languages.

This is the costliest phase. LLM calls are expensive. Every Arabic unit gets an embedding regardless of whether translation succeeds. English units are only indexed if translation succeeds.

Three sub-jobs per unit:

**Cache lookup**: Check Redis for a cached translation, keyed by `sha256(text_ar + profile_id)`. Cache hits skip the LLM call entirely.

**Translation**: If not cached, call the LLM with the translation profile appropriate for the unit's behavior. Store the result in Redis.

**Embedding**: Embed `unit.text_ar`. If translation succeeded, also embed `unit.text_en`. Both embeddings are stored on the unit.

### What it produces

- `unit.text_en` — English translation (or `None` if translation failed, with warning logged)
- `unit.embedding` — Arabic embedding vector (always present for content units)
- `unit.embedding_en` — English embedding vector (only when `text_en` is not None)

### What it must not do

- It must not silently drop units that fail to translate. Log every failure with the unit_id and reason.
- It must not generate an English embedding when `text_en` is None. There is nothing to embed.
- It must not retry indefinitely. One retry on transient errors; then log failure and continue.

### Failure modes that must raise, not fallback

- Redis is unreachable and no local cache fallback exists → raise `EnrichError`
- Embedding model returns malformed output → raise `EnrichError`
- LLM returns a response that cannot be parsed as translated text → log + mark unit as failed (not raise; translation failures are recoverable)

---

## Phase 5: GRAPH — `graph.py`

### Intent

Construct all knowledge graph edges and write them to Neo4j. Write all unit embeddings to Qdrant.

Phase 5 has access to everything: the full span tree with hierarchy, all entities, all units with their embeddings. It uses this complete picture to build the three layers of the knowledge graph.

Three sub-phases, run in order:

**Structural edges** (deterministic, `confidence=1.0`):
From hierarchy paths: `CONTAINS`, `NEXT`, `HAS_SECTION`.
From span annotations: `HAS_FOOTNOTE`.
From entity extractions: `CITATION_MENTION`, `MENTIONS_PERSON`, `HAS_SANAD`, `HAS_MATN`, `NARRATES_FROM`.
These require no inference. If hierarchy data and extractions are correct, these edges are correct.

**Citation resolution** (probabilistic, promotes `CITATION_MENTION` → `CITES`):
For each `CITATION_MENTION` edge, search Qdrant for candidate targets, score them using multi-channel evidence (text similarity, bibliographic metadata, author relationships), apply the dominance check, and promote to `CITES` if confidence ≥ `config.thresholds["citation_confidence_min"]`. Ambiguous cases remain as `CITATION_MENTION` with a candidate list attached. Unresolvable mentions are logged.

**Cross-edition alignment** (when multiple manifestations are present):
Select a canonical manifestation per work. Align non-canonical units to canonical ones via fingerprint → hierarchy+page → semantic fallback. Emit `ALIGNS_WITH` edges. Promote edges that belong to the work (not the edition) to work-level edges.

### What it produces

- All structural edges written to Neo4j
- All `CITES` edges written to Neo4j
- All `ALIGNS_WITH` edges written to Neo4j
- All unit embeddings upserted to Qdrant (`ar_units` and `en_units` collections)

### What it must not do

- It must not promote a `CITATION_MENTION` to `CITES` when the dominance check fails. An ambiguous edge that looks like a connection is worse than no connection.
- It must not write partial edge batches and report success. Either the batch writes or it raises.
- It must not skip deduplication. Edge IDs are deterministic sha256 hashes; duplicates must be detected and suppressed, not written twice.

### Failure modes that must raise, not fallback

- Neo4j is unreachable → raise `GraphError`
- Qdrant is unreachable → raise `GraphError`
- Edge construction produces a `source_id` or `target_id` that does not exist in the graph → raise `GraphError`
