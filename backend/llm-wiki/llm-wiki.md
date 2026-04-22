# LLM Wiki for Literature Review

This file defines a domain-specific pattern for **paper reading and research analysis**.
The goal is not generic note-taking, but building a persistent, evidence-traceable
literature wiki for long-term synthesis.
Boundary: the LLM does not ingest files into `raw/`; file placement is handled by local Zotero sync.

## Core idea

Classic RAG re-discovers evidence every time you ask a question.
LLM Wiki compiles knowledge into a maintained markdown codebase:

- new paper arrives -> integrate into structured pages
- conflicts are marked, not silently overwritten
- comparisons and overviews get continuously refined

Over time, answers become faster, deeper, and more consistent because synthesis is
already materialized in the wiki layer.

## Architecture (three layers)

### 1) Raw sources (immutable)

Located under `raw/` (kept consistent with local Zotero directories):

- `raw/pdf/`: original papers and reports (mirrors local Zotero PDF directory)
- `raw/markdown/`: reading notes, clipped articles, extracted text (mirrors local Zotero markdown directory)

Rules:

- append-only
- never modify existing files
- every wiki claim must trace back here
- keep folder layout and filenames aligned with local Zotero sources
- do not move/copy/import source files into `raw/`; only process files already present

### 2) Wiki (LLM-maintained)

Located under `wiki/`:

- `entities/`: paper/dataset/author/lab pages
- `concepts/`: methods, paradigms, frameworks
- `themes/`: topic-level synthesis
- `tables/`: structured comparisons
- `overviews/`: high-level landscape and roadmap

### 3) Schema (discipline)

`AGENTS.md` is the single source of behavioral rules:

- structure conventions
- citation requirements
- ingest/query/lint workflows
- conflict handling

## Operations

### Ingest

When a new paper has already been synchronized from local Zotero:

1. Read source from `raw/pdf` or `raw/markdown`
2. Extract:
   - problem statement
   - method and novelty
   - setup (datasets, metrics, baselines)
   - key quantitative results
   - limitations and open questions
3. Update impacted pages:
   - paper entity page
   - related concept page
   - theme synthesis
   - comparison tables
4. Add explicit source links for key claims
5. Mark contradictions with prior evidence

### Query

When user asks a research question:

1. Scan `wiki/entities|concepts|themes|tables|overviews`
2. Read top relevant pages (themes and tables first)
3. Compose answer with evidence links
4. Optionally write a reusable analysis page back to wiki

### Lint

Periodic maintenance:

- stale claims superseded by newer papers
- missing citations
- orphan pages
- concept pages lacking comparisons
- unresolved contradictions

## Recommended output format for answers

1. Synthesis paragraph (what we know now)
2. Comparison block (method/setting/metric)
3. Evidence list (wiki page + raw source path)
4. Open questions

## Why this is effective for research

Literature work fails when bookkeeping cost is high.
LLM handles the bookkeeping: link maintenance, table updates, contradiction tracking.
Human focuses on source curation and critical judgment.
