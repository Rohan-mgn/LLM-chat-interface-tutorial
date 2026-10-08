# Stage 9: Files, attachments and document intelligence

This stage preserves the Stage 8 chat UI, streaming, Markdown, syntax highlighting, branches, cancellation and SQLite history. It adds scoped uploads, hybrid retrieval, exact document tools, complete-document summaries, CSV/XLSX calculations and optional vision.

## Run

From the project root in PowerShell:

~~~powershell
.\.venv\Scripts\python.exe -m pip install -r "Stage 9(Files attachments and RAG)/requirements-dev.txt"
.\.venv\Scripts\python.exe "Stage 9(Files attachments and RAG)/run.py"
~~~

Keep Ollama running with your installed models. Defaults are **llama3.2:3b** and **embeddinggemma:latest**. The app never pulls models. Open http://127.0.0.1:8000; use --port 8009 if another stage uses 8000.

All paths derive from main.py, not the terminal directory:

~~~text
D:\Projects\LLM-chat-interface-tutorial\data\stage9\
  chat.db
  chat.pre-document-intelligence.db    created once before upgrading an older database
  uploads\
    <generated file ID>.<extension>
    .tmp\
~~~

Startup prints these paths. Earlier stages and their databases are unchanged. run.py watches an explicit list of application Python/HTML/CSS/JavaScript/vendor files, including the new modules. Data, tests, documentation and other stages do not trigger restarts. Refresh the browser after frontend changes.

## Using files

Attach files, drop them onto the composer, or paste supported files/images. Open **Files in this chat**, select sources and enable **Use files to answer**. Upload progress, retry, removal, preview, replacement and deletion remain available.

Parsed documents can support exact tools and lexical search even when embedding generation fails. Status messages show the active operation. Stop cancels generation, model waits and summary stages.

**Use files off applies to answer-time document access for that turn.** It bypasses document routing, retrieval, tools and vision during response generation. It does not prevent or cancel upload-time background extraction/indexing. Prior assistant messages remain history, not fresh document evidence.

| Example request | Execution |
| --- | --- |
| How many times does the word transformer occur? | Deterministic whole-document count |
| Which pages contain neural network? | Exact full-document page search |
| Find the exact phrase "termination clause" | Exact search with block offsets |
| List headings / extract emails / show page 2 | Deterministic extraction |
| Why did the authors select transformer architecture? | Hybrid retrieval, reranking and grounded answer |
| Summarize this document / summarize section Introduction | All readable source blocks, bounded map/reduce |
| Compare old_contract.pdf with new_contract.pdf | Summary preparation covering every selected document |
| What is total revenue and average revenue by region? | Validated complete CSV/XLSX calculation |
| What about Q4? | Conversation-aware retrieval using selected-branch history |

Click passage citations to preview sources. Click **Tool evidence** to inspect exact operations, file fingerprints, validated arguments, coverage, rows/cells examined, exclusions and results. Counts/calculations never manufacture fake passage citations.

Refreshing restores messages, attachments and evidence from SQLite. A full reload resets the Use files toggle so source use remains explicit. New chat and branch operations retain conversation isolation.

## Implementation

~~~mermaid
flowchart TD
  U[Upload] --> P[Validate and extract structured blocks]
  P --> V[Optional vision for unreadable pages/images]
  P --> B[SQLite canonical blocks and parsed caches]
  V --> B
  B --> I[Child chunks, embeddings and FTS5]
  Q[Question and selected-branch history] --> R{Use files?}
  R -->|No| N[Ordinary chat]
  R -->|Yes| A[Document task router]
  A --> T[Exact tools and table calculations]
  A --> H[Hybrid retrieval]
  A --> S[Full-document summaries and comparison]
  B --> T
  B --> S
  I --> H
  H --> G[Grounded streamed response and passage citations]
  S --> G
  T --> D[Deterministic response and tool provenance]
~~~

| Module | Responsibility |
| --- | --- |
| model_provider.py | Ollama chat, streaming, embeddings, structured output, optional images and resource slots |
| context_budget.py | Shared input estimates, framing and output reservations |
| document_parsers.py | Native extraction, workbook integration, lazy optional PDF rendering and vision |
| documents.py | Scoped files, canonical storage, caches, migration and evidence relationships |
| document_tools.py | Deterministic search/count/extraction and stable block IDs |
| table_tools.py | Bounded XLSX parsing and validated Decimal calculations |
| rag.py | Background indexing, FTS/dense retrieval, fusion, reranking and diversity |
| summarizer.py | Coverage-first summaries and cached intermediate reductions |
| document_agent.py | Task routing, selected-file tools, history compression and orchestration |
| main.py / file_routes.py | Existing chat lifecycle plus scoped file/evidence APIs |

tree_store.py is unchanged.

### Routing and evidence

Obvious tasks use deterministic rules. Ambiguous requests can use one bounded structured classification call. Unsupported or unclear exact/numeric operations fail explicitly instead of falling through to fabricated arithmetic.

Simple aggregates matching a complete grammar can run without model interpretation. More complex table questions use schema-constrained interpretation followed by Python validation. Document text never becomes Python, SQL, tool instructions or system instructions.

Document QA uses supplied excerpts. If evidence is insufficient, the server returns an explicit no-evidence response. Unknown citation IDs are removed. Valid IDs prove that a source was supplied, not that every generated claim follows from it; model grounding still requires evaluation.

### Canonical extraction and exact matching

Supported formats: TXT, Markdown, PDF, DOCX, CSV, XLSX, PNG, JPEG and WebP.

Blocks retain available page, heading/level, paragraph, sheet/table, row, origin and offsets. IDs hash stable file identity, logical location, block order and normalized content. Unchanged content/structure keeps IDs across compatible re-indexing.

Exact operations inspect complete extracted canonical text, not retrieval top-K:

- Unicode NFC normalization, Unicode casefold by default, and word boundaries with internal apostrophes.
- Phrase matching collapses whitespace and counts non-overlapping occurrences.
- Cross-block matches require explicit continuous logical text. Pages, headings, table rows and sheets are not blindly concatenated.
- Matches preserve source locations and character offsets.
- Character counts describe normalized canonical text, not original file bytes.
- Image descriptions are excluded from exact counts. Vision transcriptions are labeled; counts inherit any transcription errors.
- Partial extraction reports unreadable coverage rather than claiming all pages were examined.

PDF extraction tries normal pypdf extraction and falls back to layout mode for poor/failed extraction. Layout mode is not a table parser. Native page text is preserved when other pages need vision. PDF typography cannot reliably establish semantic headings or reconstruct arbitrary tables. DOCX preserves headings, paragraphs and table-row text, not full Word layout.

### CSV/XLSX calculations

Python performs sum, average, min/max, count, filtering and grouping with Decimal arithmetic. Multiple aggregates are supported. Recurring averages have bounded precision. Missing/nonnumeric aggregate values are reported; invalid arguments and ambiguous numeric input fail safely.

CSV requires unique headers. XLSX uses each sheet's first useful row as headers. Exact table analysis does not infer reliable tables from PDF layout.

Workbook preflight traverses actual serialized cells, validates worksheet relationships and bounds ZIP expansion, merged ranges and useful/physical cell counts. Styled empty regions and declared max_row/max_column dimensions do not cause rectangular traversal.

Paired workbooks are opened with data_only=False and data_only=True, both keep_links=False. Formula expressions are retained separately from cached calculated values. Formulas and external references are never executed or fetched. Missing formula caches produce an explicit error, not zero; recalculate/save in a spreadsheet application.

### Retrieval and context

Approximately 350-token children preserve structural metadata; table rows stay intact. Small parent-block context can replace a child, using an accurate parent source preview.

Up to 30 dense and 30 lexical candidates are fused with reciprocal-rank fusion (constant 60). FTS5 is feature-detected; a bounded scoped lexical fallback supports missing FTS5 or unavailable embeddings. Model digest, dimensions, parser/chunk pipeline and embedding input conventions guard compatibility.

At most 12 candidates are reranked through the existing chat model with a 15-second timeout. Invalid output/timeouts fall back to fused ranking. Duplicate suppression and an MMR-style relevance/diversity score reduce repetition. Evidence budgets are task-dependent: normally 4, broad 8, balanced retrieval up to 12, with an additional context cap.

Confidence combines lexical/dense support and reranking rather than only the old cosine cutoff. Complex questions can trigger one additional retrieval: **two rounds maximum**.

Every provider chat call checks a shared conservative estimate: about two ASCII characters/token, UTF-8 bytes for non-ASCII, message/image overhead, output reservation and a safety margin. This is not tokenizer-exact. Excessive inputs fail explicitly.

### Summaries, history and concurrency

Small documents fit one summary call; larger ones pack all readable blocks efficiently into map/reduce batches. Section summaries restrict scope explicitly. Comparisons prepare every selected file, not just whichever document dominates retrieval.

**64 model calls and 15 minutes are absolute summary safety ceilings, not target budgets.** History-compression calls count toward this limit. One call is reserved for final synthesis, whose streaming also obeys the deadline. Cancellation/progress checks run between calls and reductions. Cache entries hold parsed representations and summary intermediates, not broad natural-language answer caches.

Old branch history can be compressed into a labeled unverified recap plus recent raw turns. Recap caches are keyed by branch content. Prior assistant assertions are not authoritative document evidence.

Generation locks are per conversation: one mutation-generating operation per chat, while unrelated chats progress or queue independently. Text-model concurrency defaults to **1**, configurable after hardware testing. Async model waiters queue fairly; slots are held per call/stream rather than for an entire summary.

Native extraction, embeddings and vision have separate bounded controls. A long scanned-PDF workflow does not hold the native-text extraction gate for its entire duration. The supported launcher uses one worker.

## Optional vision

Vision is disabled unless you explicitly configure an already-installed compatible model. No model is chosen or downloaded automatically. Images still support safe preview/download without vision.

~~~powershell
# Optional, needed for scanned-PDF rendering:
.\.venv\Scripts\python.exe -m pip install -r "Stage 9(Files attachments and RAG)/requirements-vision.txt"
$env:VISION_MODEL = "your-installed-vision-model"
.\.venv\Scripts\python.exe "Stage 9(Files attachments and RAG)/run.py"
~~~

The adapter checks advertised vision capability, sends bounded image bytes, and separates transcription, description and uncertainty. Page results are cached. pypdfium2 is lazy-loaded and its operations serialized; ordinary startup/text workflows require neither it nor a vision model.

Bounds: one vision inference, image edge at most 2,048 pixels, 120 seconds per call, 200 PDF pages, and 30 minutes per vision indexing workflow. Native/vision origins remain distinct. Offline tests use deterministic vision doubles; no live OCR-accuracy claim is made without a configured model.

## Configuration and limits

| Environment variable | Default |
| --- | --- |
| CHAT_MODEL | llama3.2:3b |
| EMBED_MODEL | embeddinggemma:latest |
| VISION_MODEL | unset |
| TEXT_MODEL_CONCURRENCY | 1 |
| MODEL_CONTEXT_TOKENS | 16384; minimum 4096 |
| RAG_DEBUG | 0 |
| EMBED_DOCUMENT_PREFIX / EMBED_QUERY_PREFIX | EmbeddingGemma conventions for that family; otherwise generic input |

| Resource | Bound |
| --- | --- |
| Upload | 10 MiB each; four files and 20 MiB per message |
| Conversation | 40 files / 50 MiB |
| Extracted text | 500,000 characters per document |
| Retrieval index | 2,000 chunks/document; 4,000/conversation |
| PDF / image | 200 pages / 20 megapixels before resizing |
| Expanded DOCX/XLSX | 20 MiB |
| Workbook | 100,000 useful cells; 200,000 physical cells; 20 sheets; 100 useful columns |
| Table results | 200 groups; 8 aggregates; 10 filters |
| Final generation | 2,048 reserved output tokens |

This remains a local single-user tutorial app bound to 127.0.0.1, not an authenticated public service or multi-worker deployment.

## Migration and evidence lifecycle

SQLite's backup API creates chat.pre-document-intelligence.db once before upgrading an older database. Document migrations are repeatable and check foreign keys. Existing messages, branches, file associations and valid citation relationships are preserved.

New tables: document_schema, document_blocks, document_cache, history_summaries, block_citations, tool_runs, tool_run_files, plus chunks_fts when available. files gains parse_state, coverage and parser_config. citations gains a first-appearance ordinal.

Old incompatible files show reindex_required. Use **Re-index**. Unchanged compatible passages keep source IDs. Changed/replaced/deleted sources invalidate obsolete previews, even if re-embedding fails. Old assistant text remains; missing passage citations show **source unavailable** and never redirect to new content.

Deterministic provenance is separate from passage evidence. Removing a source removes its inspectable tool records and file-owned caches. Physical deletion uses the existing transactional pending_unlinks outbox. Migration backups remain separate snapshots; manage them explicitly if erasing historical data later.

With RAG_DEBUG=0, diagnostics do not duplicate full retrieved context; startup removes legacy diagnostic context. Necessary previews and tool provenance remain. RAG_DEBUG=1 permits scoped full diagnostics. Replacing/deleting files clears corresponding conversation diagnostics.

## Dependencies and API

Core additions: **openpyxl==3.1.5**, **defusedxml==0.7.1**. Optional scanned-PDF renderer: **pypdfium2==5.14.0** in requirements-vision.txt only.

**ollama==0.6.3 is retained.** Its local client types support every used image, JSON-schema structured-output, streaming and embedding API; a client upgrade was unnecessary.

The existing chat/generation/branch API and SSE start/status/delta/error/done events remain. The frontend sends the newest message, attachment_ids, use_files and file_ids; backend SQLite owns history. Edit/retry/regenerate retain original turn scope.

| Route | Purpose |
| --- | --- |
| GET /files/config | Limits, model configuration and debug availability |
| GET/POST /conversations/{id}/files | List or upload multipart field upload |
| PUT/DELETE .../files/{file_id} | Replace/delete |
| POST .../files/{file_id}/reindex | Retry/rebuild |
| GET .../files/{file_id}/download | Safe attachment; only validated images support inline preview |
| GET .../files/{file_id}/preview | Bounded extracted-text preview |
| GET /conversations/{id}/sources/{source_id} | Passage/block preview |
| GET /conversations/{id}/tool-evidence/{tool_id} | Deterministic provenance |
| GET /conversations/{id}/messages/{message_id}/retrieval | Debug-only diagnostics |

## Verification

From the Stage 9 directory:

~~~powershell
..\.venv\Scripts\python.exe -B tests/check_database.py
..\.venv\Scripts\python.exe -B tests/check_rag.py
..\.venv\Scripts\python.exe -B tests/check_document_intelligence.py
..\.venv\Scripts\python.exe -B tests/check_browser.py
..\.venv\Scripts\python.exe -B tests/check_browser.py --files
..\.venv\Scripts\python.exe -B tests/check_browser.py --responses
..\.venv\Scripts\python.exe -B tests/check_reload.py
~~~

These use disposable databases, deterministic model doubles and local Edge. No model download or internet is required. They cover scope/isolation, conflicts, independent native indexing during vision, exact tools, formula caches, summaries, lexical fallback, migration, citation invalidation, context limits and cancellation.

Live evaluation is separate:

~~~powershell
..\.venv\Scripts\python.exe -B tests/check_live_rag.py
~~~

It reports Hit@K, expected-file recall, MRR, retrieval/answer latency and citation-ID validity; it checks follow-ups, isolation, exact counts/table totals and restart persistence. Inspect answers for factual accuracy, citation entailment and coverage. Valid source IDs alone do not prove those qualities.


## Upgrade change inventory and verification record

Created: context_budget.py, document_agent.py, document_parsers.py, document_tools.py, model_provider.py, summarizer.py, table_tools.py, requirements-vision.txt and tests/check_document_intelligence.py.

Updated: main.py, documents.py, rag.py, file_routes.py, run.py, requirements.txt, static/index.html, static/files.js, README.md, tests/check_rag.py, tests/check_live_rag.py and tests/file_checks.js. The parser/context-budget modules are focused additions beyond the initial suggested file list. No tree_store.py or earlier-stage changes were needed.

Verification through 8 October 2026:

- 26 database/conversation tests and 22 existing file/RAG tests passed.
- 33 document-intelligence tests passed across the full suite and targeted final-fix runs (81 distinct offline tests total).
- Desktop/mobile file tests passed, including deterministic evidence inspection and unavailable deleted citations.
- Desktop/mobile general chat and long-response/code-rendering suites passed; runtime reload inclusion/exclusion tests passed.
- The final live run passed retrieval, cited answering, follow-up isolation, exact count, table total and restart checks. Its three-question retrieval fixture achieved Hit@K=1.0, file recall=1.0 and MRR=1.0; citation-ID validity was 1.0. This small fixture does not establish general model accuracy.
- Initial live attempts encountered unavailable Ollama and an embedding fingerprint change during its update. Once Ollama was stable, a fresh isolated run passed. The harness reports missing local prerequisites separately and prints index compatibility diagnostics on mismatch. Existing documents indexed with an older model fingerprint need Re-index.
- Optional vision paths were tested with deterministic doubles, not a downloaded/configured live vision model.
- git diff --check passed; final edits are limited to Stage 9.
