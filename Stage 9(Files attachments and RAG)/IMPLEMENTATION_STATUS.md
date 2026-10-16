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

Pending: qualification and production integration of the new bounded semantic planner/executor, full adaptive direct-context selection beyond current complete-document routes, complete shared tool/evidence contract, independent OCR and optional format processors, richer branch-aware answer references, and end-to-end attachment quality acceptance. Existing routing remains until the validated replacement is integrated.

The final combined regression passed **190 tests**, with zero failures/errors,
in 304.801 seconds including discovery. Runtime source hashes were unchanged.
See `tests/results/answering-verified-regression.json`.

The revised live development smoke test completed four requests, but **did not
pass answer-quality acceptance**. All four answers omitted citations and the
coverage check flagged zero linked sections. The direct child explanation also
used misleading hot-chocolate/gas and vapor-filled-bucket analogies. Expected
term presence is not factual correctness. Full outputs remain in
`tests/results/complete-context-revised.json`; initial routing failures are retained.

| Scenario | Hierarchical calls / total / first emission | Direct calls / total / first emission |
| --- | --- | --- |
| Child water-cycle lesson | 2 / 112.873 s / 79.368 s | 1 / 77.458 s / 30.095 s |
| Maintenance study guide | 2 / 107.489 s / 63.255 s | 1 / 112.124 s / 34.240 s |

These are single observations with overlapping regression work, not a stable
speedup claim. Reported input/output tokens were 1484/401 versus 1630/190 for the
water lesson and 1472/410 versus 1592/307 for the maintenance guide. Direct context
removes an intermediate call but carries more raw source metadata into generation.

## Measurements and limitations

- Development retrieval Hit@K/Recall@K/MRR/nDCG: 0.95238 for both storage backends (21 retrieval cases). Held-out: 0.85 (20 cases).
- Development median end-to-end latency: SQLite 13.340 s; Lance 16.351 s. Held-out: 10.926 s versus 12.219 s. Median model calls stayed at two. No answer-latency improvement is claimed.
- Numeric search benchmarks used 30 process-cold and 30 warm repetitions at 100, 1,000, 4,000 and selectively filtered 40,000 chunks. Top-30 rankings agreed. See VECTOR_STORAGE.md for latency/memory tradeoffs.
- Arrow-batched ingestion measured 289.926 MiB at 4,000 chunks and 335.418 MiB at 40,000, below the unchanged 512 MiB acceptance limit.
- Live answer-term/abstention checks are heuristics, not faithfulness proofs. Development abstention passed 5/9; held-out 6/10 for each backend.
- Windows native latest-version-hint rename warnings occurred despite passing manifest/visibility tests.
- No frontier-assistant reference outputs have been supplied; no equivalence or completed attachment-quality claim is made.

See [VECTOR_STORAGE.md](VECTOR_STORAGE.md) for commands and measured storage boundaries.

## Planner and tool-binding checkpoint

Implemented in RAG/planner.py and RAG/attachment_tools.py:
- Structured and native decision protocols; application-owned, capability-scoped tools.
- Four decision steps, eight-tool ceiling, two-search ceiling, one repair, cancellation
  and bounded deadlines. Failed summary calls remain counted across tool retries.
- Actual canonical reading, hybrid search, table inspection/calculation, code inspection
  without execution, and existing hierarchical processing.
- Per-turn file aliases and revision checks before/after every tool. Search results
  rehydrate text, locations and filenames from SQLite before planner exposure.
- Separate passage and deterministic-tool provenance; stable session citation labels.
- Thirteen tool-binding tests passed, including actual planner/executor integration,
  forged metadata, source changes, failed-attempt limits and exact calculations.

The first complete regression passed 214 tests in 367.841 seconds, with unchanged
runtime hashes. A final run follows the additional hydration and label tests;
its result is recorded separately rather than overwriting that baseline.

The 40-decision development probe is complete. Structured decisions: 19/20 valid,
10/20 correct action/scope. Native: 8/20 valid, 7/20 correct. Frozen requirements
were 95% validity and 90% selection. Neither qualified, so **production routing
is unchanged**. Native argument failures, spreadsheet routing and prior-answer
references remain open problems. These are first-decision fixtures, not full
assistant acceptance or unsafe-execution measurements.

Compact evidence retained necessary locations and extraction warnings while
removing internal hashes/parser bookkeeping from prompts. Live direct probes
reported 909/907 input tokens versus earlier 1630/1592; elapsed observations were
113.840/81.343 seconds. Linked section coverage remained 0/3 and 1/3.
This is prompt-size reduction, not a quality or latency acceptance claim.

A further development-only citation-format example was evaluated with the same
two documents. Both still had 0/3 linked sections. The child explanation called
evaporation 'water turns into air'; the maintenance guide lacked a practice
question in its shutdown section and added an unsupported causal explanation.
The candidate was not adopted. Full outputs are retained in
tests/results/citation-candidate-live.json. Neither candidate nor baseline
demonstrates frontier-assistant answer quality.

See PLANNER_QUALIFICATION.md for thresholds, commands and integration gates.
Live status was rechecked: LanceDB, epoch 1, CUTOVER_ACTIVE. No finalization,
rollback, model change, dependency installation or production-data mutation
was performed in this checkpoint.
