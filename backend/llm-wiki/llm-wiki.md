# LLM Wiki for Literature Review

This file defines a domain-specific pattern for **paper reading and research analysis**.
The goal is not generic note-taking, but building a persistent, evidence-traceable
literature wiki for long-term synthesis.
Boundary: the LLM does not ingest files into `raw/`; file placement is handled by local Zotero sync.

## Core idea

Classic RAG re-discovers evidence every time you ask a question.
LLM Wiki compiles knowledge into a maintained markdown codebase:

- new paper arrives -> one mirrored markdown page under `wiki/` plus optional updates to cross-paper synthesis files
- conflicts are marked, not silently overwritten
- cross-paper comparisons and field-level overviews live under `wiki/_synthesis/` and are kept evidence-linked

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

- **Mirrored pages**: for `raw/markdown/<relpath>/<name>.md` (often produced from `raw/pdf/<relpath>/<name>.pdf`), maintain **exactly one** `wiki/<relpath>/<name>.md` (**same tree and basename as markdown**). Inside that file, use the section layout in `wiki/_templates/paper.md` (entity, concepts, per-paper theme notes, per-paper comparisons, overview/navigation, sources).
- **`wiki/_synthesis/`**: cross-paper artifacts (topic surveys, multi-paper tables, roadmaps). Each file must cite backing `raw/...` paths and relevant per-paper `wiki/.../*.md` links.

### 3) Schema (discipline)

`AGENTS.md` is the single source of behavioral rules:

- structure conventions
- citation requirements
- ingest/query/lint workflows
- conflict handling

## Operations

### Ingest

When a new paper has already been synchronized from local Zotero:

1. Read factual text from `raw/markdown` (PDF pipeline lands here first)
2. Extract:
   - problem statement
   - method and novelty
   - setup (datasets, metrics, baselines)
   - key quantitative results
   - limitations and open questions
3. Create or update the **mirrored** `wiki/<relpath>/<stem>.md` for that PDF (see template)
4. If the finding affects multiple papers or field-level themes, update or add files under `wiki/_synthesis/`
5. Add explicit source links for key claims (**`raw/markdown/...`**, mirroring the wiki path; note pending PDF only if `.md` does not exist yet)
6. Mark contradictions with prior evidence (preferably in `_synthesis` with backlinks to per-paper pages)

### Query

When user asks a research question:

1. Check `wiki/_synthesis/` for existing surveys or comparison tables
2. Read relevant mirrored per-paper pages under `wiki/`
3. Compose answer with evidence links (`raw` + `wiki`)
4. Optionally write reusable synthesis back to `_synthesis/` or update the per-paper page

### Lint

Periodic maintenance:

- stale claims superseded by newer papers
- missing citations
- Markdown files under `raw/markdown` missing a mirrored `wiki/**/*.md`
- synthesis files lacking evidence links
- unresolved contradictions

## Recommended output format for answers

1. Synthesis paragraph (what we know now)
2. Comparison block (method/setting/metric) with links to `_synthesis` or tables therein
3. Evidence list (mirrored wiki page + **`raw/markdown/...`** path as primary traceability)
4. Open questions

## Why this is effective for research

Literature work fails when bookkeeping cost is high.
LLM handles the bookkeeping: link maintenance, table updates, contradiction tracking.
Human focuses on source curation and critical judgment.
