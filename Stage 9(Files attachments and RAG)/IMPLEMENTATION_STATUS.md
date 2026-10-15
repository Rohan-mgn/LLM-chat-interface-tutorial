# Stage 9 implementation status

This is an implementation checkpoint, not a completion claim. Changes are confined to Stage 9.

## Live storage and baseline

- Original regression baseline: 104 tests passed in 52.752 seconds.
- Pre-cutover storage regression: 157 tests passed in 219.732 seconds, with unchanged source hashes during the run.
- Native environment: Windows 11, Python 3.14.3, LanceDB 0.40.0.
- Live backend is **LanceDB, CUTOVER_ACTIVE, epoch 1**. The verified cutover transferred 63 sources across four files without re-embedding. One conversation and 11 messages, document records, branches and citations matched the verified backup. SQLite integrity and foreign-key checks passed.
- `tests/results/live-cutover.json` and `storage-cutover-acceptance.json` record the actual transition and its acceptance evidence.
- Model defaults and 16,384-token allocation remain unchanged. No models were downloaded.

## Milestone 1 - validated cutover; live cleanup pending

LanceDB is the sole active semantic retrieval engine. SQLite remains authoritative for application data, documents, source records and citations. SQLite FTS5 remains the lexical backend: a 13-query compatibility fixture matched 12 queries; Lance did not reproduce SQLite accent folding on the remaining query.

Implemented and exercised: filtered exact vector search, authoritative hydration, epochs and OS-backed leases, generation manifests, idempotent writes, crash recovery, deletion reconciliation, exact-digest rebuilding, current-data rollback, coordinated recovery metadata, maintenance and explicit retention-guarded finalization/retirement commands.

Legacy SQLite vector data is retained for rollback and receives no new active-backend embeddings. **Live finalization and backup retirement have not occurred.** They require at least 14 days of stabilization/retention and separate explicit decisions. Passing cleanup tests on fixtures does not complete this live milestone.

## Milestone 2 - incremental implementation

Implemented:

- Source-revision capture before document tools/model work, per-conversation cross-process release gates shared with source mutations, bounded output queues and five-second ASGI send deadlines.
- Revocation cancels generation and discards unsent text. Only successfully server-emitted text is checkpointed and persisted; stopped responses cannot become completed reusable operations. ASGI send completion does not prove browser display.
- Eleven release/revocation tests passed, including a separate process deleting a source during an in-flight send, queued-output invalidation, permission changes, disconnects, replacement and finalization races.
- A versioned parser registry; native parsing runs in terminable workers. Windows workers have 512 MiB memory, 25-second CPU, 30-second wall, one-process and bounded-output limits.
- Existing PDF/DOCX/XLSX/CSV/text/image paths preserved. New PPTX extraction includes slide order, text, notes, tables, cached chart data and image metadata; visual interpretation is not inferred from these.
- A full 179-test parser/release/storage regression passed in 283.650 seconds with unchanged runtime sources.
- New source formats preserve lines and whitespace. Python uses AST inspection without execution; JSON/XML use safe structural parsing. JavaScript/TypeScript/CSS and YAML currently expose source text, not full semantic parsing.
- Direct slide/line/code-inspection tools and additive file capability metadata.

- Complete-document tasks now try all-or-nothing direct context first, using authoritative canonical blocks and the unchanged context budget. This works for parsed content even if semantic indexing is unavailable. Targeted QA still uses the existing retrieval path.
- Shared final-answer instructions preserve the original task, audience and format. Hierarchical notes carry the exact objective through each reduction, and summary cache keys include that task. Nine focused tests passed before the final regression.
- Versioned evidence diagnostics record source identity/fingerprint; a structural coverage map links source sections to substantive cited answer spans and explicitly leaves semantic completeness unverified. Missing links and partial extraction produce advisory text that is streamed and persisted identically.

Pending: bounded semantic planner/executor, full adaptive direct-context selection beyond current complete-document routes, complete shared tool/evidence contract, independent OCR and optional format processors, richer branch-aware answer references, and end-to-end attachment quality acceptance. Existing keyword routing remains until the validated replacement is integrated.

A new two-scenario live development smoke comparison is being recorded separately. It is not the full attachment evaluation or a frontier comparison. Its initial water-cycle request exposed an overbroad section selector; failed attempts are retained and do not count as model-quality passes.

The passing parser/release regression is in `tests/results/attachment-regression.json`. The later answering integration run is recorded separately in `tests/results/answering-regression.json`. Do not interpret partial logs as passing runs.

## Measurements and limitations

- Development retrieval Hit@K/Recall@K/MRR/nDCG: 0.95238 for both storage backends (21 retrieval cases). Held-out: 0.85 (20 cases).
- Development median end-to-end latency: SQLite 13.340 s; Lance 16.351 s. Held-out: 10.926 s versus 12.219 s. Median model calls stayed at two. No answer-latency improvement is claimed.
- Numeric search benchmarks used 30 process-cold and 30 warm repetitions at 100, 1,000, 4,000 and selectively filtered 40,000 chunks. Top-30 rankings agreed. See VECTOR_STORAGE.md for latency/memory tradeoffs.
- Arrow-batched ingestion measured 289.926 MiB at 4,000 chunks and 335.418 MiB at 40,000, below the unchanged 512 MiB acceptance limit.
- Live answer-term/abstention checks are heuristics, not faithfulness proofs. Development abstention passed 5/9; held-out 6/10 for each backend.
- Windows native latest-version-hint rename warnings occurred despite passing manifest/visibility tests.
- No frontier-assistant reference outputs have been supplied; no equivalence or completed attachment-quality claim is made.

See [VECTOR_STORAGE.md](VECTOR_STORAGE.md) for commands and measured storage boundaries.
