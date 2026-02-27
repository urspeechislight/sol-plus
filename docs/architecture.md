# SOL-NEXT Architecture

## About This Project

SOL-NEXT is a pipeline that transforms digitized classical Arabic manuscripts into a structured, searchable, multilingual knowledge graph. A scholar reading Ibn Hajar's *Fath al-Bari* can follow a hadith's isnad back through the narrators, trace its parallel transmissions across other collections, read the Arabic alongside an English translation, and discover which later scholars cited the same hadith.

The pipeline replaces ~47,000 lines of Python (the original `sol` codebase) with ~3,800 lines by collapsing intermediate representations and moving all domain knowledge into a single YAML configuration file.

---

## 1. Pipeline Overview

The entire pipeline is five function calls. Each phase reads from and writes to a single `Manuscript` object passed between them.

```mermaid
flowchart LR
    subgraph Input
        JSON["book.json<br/><i>digitized manuscript</i>"]
    end

    subgraph "Phase 1: INGEST"
        P1["Load JSON<br/>Normalize text<br/>Classify pages"]
    end

    subgraph "Phase 2: SEGMENT"
        P2["Split paragraphs<br/>Detect patterns<br/>Route behaviors<br/>Track hierarchy"]
    end

    subgraph "Phase 3: EXTRACT"
        P3["Run extractors<br/>Atomicize spans<br/>→ entities + units"]
    end

    subgraph "Phase 4: ENRICH"
        P4["Translate units<br/>Generate embeddings<br/><i>(async, cached)</i>"]
    end

    subgraph "Phase 5: GRAPH"
        P5["Build edges<br/>Resolve citations<br/>Align editions"]
    end

    subgraph Storage
        NEO4J[(Neo4j)]
        QDRANT[(Qdrant)]
    end

    JSON --> P1
    P1 -->|".pages"| P2
    P2 -->|".spans"| P3
    P3 -->|".entities .units"| P4
    P4 -->|".text_en .embedding"| P5
    P5 -->|"edges"| NEO4J
    P5 -->|"vectors"| QDRANT

    style P1 fill:#2d6a4f,color:#fff
    style P2 fill:#2d6a4f,color:#fff
    style P3 fill:#6c757d,color:#fff,stroke-dasharray: 5 5
    style P4 fill:#6c757d,color:#fff,stroke-dasharray: 5 5
    style P5 fill:#6c757d,color:#fff,stroke-dasharray: 5 5
```

> **Green** = implemented. **Grey dashed** = stub (contract defined, not yet built).

---

## 2. Data Model

One `Manuscript` carries everything. One `Span` type is progressively enriched — never rewrapped.

```mermaid
classDiagram
    class Manuscript {
        +str work_id
        +str manifestation_id
        +list~Page~ pages
        +list~Span~ spans
        +list~Edge~ edges
        +dict metadata
        +units() list~Unit~
        +entities() list~Entity~
    }

    class Page {
        +int page_number
        +str page_name
        +str text
        +str? footnote
        +bool is_content
    }

    class Span {
        +str span_id
        +str text
        +int page_start
        +int page_end
        +str span_type
        +list~Pattern~ patterns
        +str? behavior
        +HierarchyPath? hierarchy
        +list~Entity~? entities
        +list~Unit~? units
        +str? footnote_text
        +dict metadata
    }

    class Pattern {
        +str pattern_id
        +str matched_text
        +int char_start
        +int char_end
        +dict metadata
    }

    class HierarchyPath {
        +list~str~ path
        +list~str~ path_ids
        +int depth
    }

    class Entity {
        +str entity_id
        +str entity_type
        +str text
        +int char_start
        +int char_end
        +dict metadata
    }

    class Unit {
        +str unit_id
        +str text_ar
        +str unit_type
        +str behavior
        +str span_id
        +int page_start
        +int page_end
        +HierarchyPath hierarchy
        +str? text_en
        +list~float~? embedding
        +dict metadata
    }

    class Edge {
        +str edge_id
        +str edge_type
        +str source_id
        +str target_id
        +float confidence
        +dict evidence
    }

    Manuscript "1" *-- "*" Page : pages
    Manuscript "1" *-- "*" Span : spans
    Manuscript "1" *-- "*" Edge : edges
    Span "1" *-- "*" Pattern : patterns
    Span "1" *-- "0..1" HierarchyPath : hierarchy
    Span "1" *-- "*" Entity : entities
    Span "1" *-- "*" Unit : units
    Unit "1" --> "1" HierarchyPath : hierarchy
```

---

## 3. Span Progressive Enrichment

A `Span` is created in Phase 2 and enriched through Phase 5. It is never replaced or wrapped — downstream phases populate additional fields on the same object.

```mermaid
flowchart TB
    subgraph "Phase 2: SEGMENT"
        S2["Span created<br/>─────────────<br/>span_id ✓<br/>text ✓<br/>page_start/end ✓<br/>span_type ✓<br/>patterns ✓<br/>behavior ✓<br/>hierarchy ✓<br/>entities = None<br/>units = None"]
    end

    subgraph "Phase 3: EXTRACT"
        S3["Span updated<br/>─────────────<br/>span_id ✓<br/>text ✓<br/>page_start/end ✓<br/>span_type ✓<br/>patterns ✓<br/>behavior ✓<br/>hierarchy ✓<br/><b>entities ✓</b><br/><b>units ✓</b>"]
    end

    subgraph "Phase 4: ENRICH"
        S4["Units updated<br/>─────────────<br/>unit.text_ar ✓<br/><b>unit.text_en ✓</b><br/><b>unit.embedding ✓</b>"]
    end

    subgraph "Phase 5: GRAPH"
        S5["Edges emitted<br/>─────────────<br/>structural edges<br/>citation edges<br/>alignment edges"]
    end

    S2 -->|"same Span object"| S3
    S3 -->|"same Span object"| S4
    S4 -->|"reads span data"| S5

    style S2 fill:#2d6a4f,color:#fff
    style S3 fill:#6c757d,color:#fff,stroke-dasharray: 5 5
    style S4 fill:#6c757d,color:#fff,stroke-dasharray: 5 5
    style S5 fill:#6c757d,color:#fff,stroke-dasharray: 5 5
```

Check which phase a span has been through:
- Phase 2 complete → `span.behavior is not None`
- Phase 3 complete → `span.units is not None`
- Phase 4 complete → `span.units[0].embedding is not None`

---

## 4. Config-Driven Behavior Routing (Phase 2)

All domain knowledge lives in `config/sol.yaml`. Python executes the routing — it never contains patterns or thresholds.

```mermaid
flowchart TD
    TEXT["Span text<br/><i>Arabic paragraph</i>"]

    subgraph "Pattern Detection"
        R1["ATTRIBUTION<br/><code>حدثنا|أخبرنا|روى</code>"]
        R2["QURAN_REF<br/><code>قال الله|﴿|سورة</code>"]
        R3["HEADING_MARKER<br/><code>^كتاب|^باب|^فصل</code>"]
        R4["PERSON_REF<br/><code>الشيخ|الإمام|الحافظ</code>"]
        R5["DEATH_MARKER<br/><code>توفي|مات|وفاته</code>"]
        R6["... 7 more patterns"]
    end

    TEXT --> R1
    TEXT --> R2
    TEXT --> R3
    TEXT --> R4
    TEXT --> R5
    TEXT --> R6

    subgraph "Behavior Routing Table<br/><i>sorted by priority descending</i>"
        B95["95: SECTION_HEADING<br/><i>requires: HEADING_MARKER</i>"]
        B90["90: QURAN_VERSE<br/><i>requires: QURAN_REF</i>"]
        B85["85: HADITH_MATN<br/><i>requires: MATN_BOUNDARY_HINT</i>"]
        B80["80: HADITH_ISNAD<br/><i>requires: ATTRIBUTION</i>"]
        B75["75: FIQH_RULING<br/><i>requires: FIQH_RULING</i>"]
        B70["70: BIOGRAPHY<br/><i>requires: PERSON_REF<br/>any_of: DEATH/BIRTH/GENEALOGY</i>"]
        B60["60: NUMBERED_ENTRY<br/><i>requires: NUMBERED_ENTRY</i>"]
        B0["0: GENERAL_PROSE<br/><i>fallback</i>"]
    end

    R1 & R2 & R3 & R4 & R5 & R6 -->|"matched<br/>patterns"| B95
    B95 --> RESULT["First match wins<br/>→ span.behavior"]

    style RESULT fill:#2d6a4f,color:#fff
```

---

## 5. Phase 2 Internal Flow

The most complex implemented phase. Splits pages into spans, detects patterns, routes behaviors, and tracks document hierarchy via a finite state machine.

```mermaid
flowchart TD
    MS["Manuscript<br/><i>with .pages from Phase 1</i>"]

    COMPILE["Compile all pattern regexes<br/><i>once at startup</i>"]
    PARSE["Parse behavior rules<br/><i>sorted by priority desc</i>"]

    MS --> COMPILE
    MS --> PARSE

    subgraph "For each Page"
        SPLIT["Split on \\n\\n<br/>→ paragraphs"]
        FILTER["Filter: len ≥ min_span_chars<br/><i>threshold from config</i>"]

        subgraph "For each Paragraph"
            DETECT["Run all compiled regexes<br/>→ list of Pattern objects"]
            ROUTE["Walk routing table<br/>→ behavior label"]
            HIER["Update hierarchy FSM<br/>if SECTION_HEADING"]
            CREATE["Create Span<br/>id: {manifestation_id}_s{idx:04d}"]
        end

        SPLIT --> FILTER
        FILTER --> DETECT
        DETECT --> ROUTE
        ROUTE --> HIER
        HIER --> CREATE
    end

    COMPILE --> SPLIT
    PARSE --> ROUTE

    CREATE --> OUT["Manuscript.spans populated"]

    style MS fill:#2d6a4f,color:#fff
    style OUT fill:#2d6a4f,color:#fff
```

---

## 6. Hierarchy Tracking FSM

Phase 2 tracks document structure using a `KitabBabFaslTracker`. When a `SECTION_HEADING` span is detected, the FSM updates its state based on the heading keyword.

```mermaid
stateDiagram-v2
    [*] --> Root: pipeline start

    Root --> Kitab: "كتاب" detected
    Kitab --> Bab: "باب" detected
    Bab --> Fasl: "فصل" detected

    Kitab --> Kitab: new "كتاب"<br/>(resets bab, fasl)
    Bab --> Bab: new "باب"<br/>(resets fasl)
    Fasl --> Fasl: new "فصل"

    Kitab --> Kitab: new "كتاب"
    Bab --> Kitab: new "كتاب"<br/>(resets all)
    Fasl --> Kitab: new "كتاب"<br/>(resets all)
    Fasl --> Bab: new "باب"<br/>(resets fasl)

    note right of Root
        Every non-heading span
        inherits the current
        HierarchyPath state
    end note
```

Example hierarchy path for a span inside بَابُ الغُسْلِ within كِتَابُ الطَّهَارَةِ:

```
HierarchyPath(
    path=["كتاب الطهارة", "باب الغسل"],
    path_ids=["kitab_001", "bab_002"],
    depth=2
)
```

---

## 7. Phase 1 (Ingest) Flow

```mermaid
flowchart TD
    JSON["book.json"]

    subgraph "Phase 1: ingest(path)"
        LOAD["Load & parse JSON"]
        VALIDATE["Validate required fields<br/><i>work_id, book_id, content, pages</i>"]
        META["Extract metadata<br/><i>title, author, book_type, genre</i>"]

        subgraph "For each raw page"
            NORM["Normalize text<br/><i>NFC, strip diacritics,<br/>strip tatweel, collapse whitespace</i>"]
            FOOT["Extract footnote<br/><i>if present</i>"]
            CLASS["Classify page<br/><i>content vs frontmatter</i>"]
            PAGE["Create Page object"]
        end

        BUILD["Build Manuscript<br/><i>work_id, manifestation_id,<br/>pages, metadata</i>"]
    end

    JSON --> LOAD
    LOAD --> VALIDATE
    VALIDATE -->|"IngestError<br/>if invalid"| ERR["RAISE"]
    VALIDATE -->|"valid"| META
    META --> NORM
    NORM --> FOOT
    FOOT --> CLASS
    CLASS --> PAGE
    PAGE --> BUILD

    BUILD --> OUT["Manuscript<br/><i>with .pages populated</i>"]

    style OUT fill:#2d6a4f,color:#fff
    style ERR fill:#d32f2f,color:#fff
```

---

## 8. Planned Storage Architecture (Phase 5)

```mermaid
flowchart TB
    subgraph "Phase 5 Output"
        EDGES["Edges"]
        VECTORS["Embeddings"]
    end

    subgraph "Storage Layer"
        NEO4J[("Neo4j<br/><i>knowledge graph</i><br/>─────────<br/>structural edges<br/>citation edges<br/>hadith edges<br/>alignment edges")]
        QDRANT[("Qdrant<br/><i>vector search</i><br/>─────────<br/>ar_units collection<br/>en_units collection")]
        REDIS[("Redis<br/><i>cache</i><br/>─────────<br/>translation cache<br/>key: sha256(text+profile)")]
        PG[("PostgreSQL<br/><i>metadata store</i><br/>─────────<br/>manuscripts<br/>processing status")]
    end

    EDGES --> NEO4J
    VECTORS --> QDRANT

    subgraph "Phase 4 uses"
        P4_CACHE["Cache check<br/>before LLM call"]
    end

    P4_CACHE <--> REDIS

    subgraph "External Services"
        CLAUDE_API["Anthropic Claude API<br/><i>unit translation</i>"]
        OPENAI_API["OpenAI API<br/><i>embeddings</i>"]
    end

    P4_CACHE -.->|"cache miss"| CLAUDE_API
    CLAUDE_API -.->|"text_en"| P4_CACHE
    P4_CACHE -.-> OPENAI_API
    OPENAI_API -.->|"embedding vector"| VECTORS

    style NEO4J fill:#4a90d9,color:#fff
    style QDRANT fill:#4a90d9,color:#fff
    style REDIS fill:#e67e22,color:#fff
    style PG fill:#4a90d9,color:#fff
```

---

## 9. Knowledge Graph Edge Types

```mermaid
flowchart TD
    subgraph "Structural<br/><i>confidence = 1.0, deterministic</i>"
        CONTAINS["CONTAINS<br/><i>manuscript → span</i>"]
        NEXT["NEXT<br/><i>span → span</i>"]
        HAS_SECTION["HAS_SECTION<br/><i>section → subsection</i>"]
        HAS_FOOTNOTE["HAS_FOOTNOTE<br/><i>span → footnote span</i>"]
    end

    subgraph "Reference<br/><i>confidence varies</i>"
        CITATION["CITATION_MENTION<br/><i>span mentions a work</i>"]
        CITES["CITES<br/><i>resolved citation<br/>confidence ≥ 0.85</i>"]
        MENTIONS["MENTIONS_PERSON<br/><i>span → person entity</i>"]
    end

    subgraph "Hadith<br/><i>confidence = 1.0</i>"
        HAS_SANAD["HAS_SANAD<br/><i>hadith → isnad chain</i>"]
        HAS_MATN["HAS_MATN<br/><i>hadith → prophetic text</i>"]
        NARRATES["NARRATES_FROM<br/><i>narrator → narrator</i>"]
    end

    subgraph "Alignment<br/><i>cross-edition</i>"
        ALIGNS["ALIGNS_WITH<br/><i>unit ↔ unit<br/>confidence ≥ 0.90</i>"]
    end

    style CONTAINS fill:#2d6a4f,color:#fff
    style NEXT fill:#2d6a4f,color:#fff
    style HAS_SECTION fill:#2d6a4f,color:#fff
    style HAS_FOOTNOTE fill:#2d6a4f,color:#fff
    style CITES fill:#b8860b,color:#fff
    style ALIGNS fill:#6a0dad,color:#fff
```

---

## 10. Implementation Status

```mermaid
flowchart LR
    subgraph "Done ✓"
        MODELS["Models<br/><i>8 dataclasses</i>"]
        EXCEPTIONS["Exceptions<br/><i>6 types, 1 file</i>"]
        CONFIG["Config<br/><i>sol.yaml + loader</i>"]
        TEXT["Text Utils<br/><i>Arabic normalization</i>"]
        PH1["Phase 1: Ingest<br/><i>188 lines</i>"]
        PH2["Phase 2: Segment<br/><i>347 lines</i>"]
        TESTS1["Tests: Phase 1<br/><i>24 test cases</i>"]
    end

    subgraph "Stub"
        PH3["Phase 3: Extract"]
        PH4["Phase 4: Enrich"]
        PH5["Phase 5: Graph"]
    end

    subgraph "Empty"
        CLI["CLI entry point"]
        DB["DB layer"]
        TESTS25["Tests: Phases 2-5"]
        SCRIPTS["Scripts"]
    end

    style MODELS fill:#2d6a4f,color:#fff
    style EXCEPTIONS fill:#2d6a4f,color:#fff
    style CONFIG fill:#2d6a4f,color:#fff
    style TEXT fill:#2d6a4f,color:#fff
    style PH1 fill:#2d6a4f,color:#fff
    style PH2 fill:#2d6a4f,color:#fff
    style TESTS1 fill:#2d6a4f,color:#fff
    style PH3 fill:#6c757d,color:#fff,stroke-dasharray: 5 5
    style PH4 fill:#6c757d,color:#fff,stroke-dasharray: 5 5
    style PH5 fill:#6c757d,color:#fff,stroke-dasharray: 5 5
    style CLI fill:#d32f2f,color:#fff
    style DB fill:#d32f2f,color:#fff
    style TESTS25 fill:#d32f2f,color:#fff
    style SCRIPTS fill:#d32f2f,color:#fff
```
