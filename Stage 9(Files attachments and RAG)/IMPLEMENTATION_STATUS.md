# Stage 9 implementation status

This is an implementation checkpoint, not a completion claim. Changes are confined to Stage 9.

Latest verification: **233 tests passed**, zero failures/errors, 317.925 seconds
including discovery. Runtime source hashes stayed unchanged during the run.
See [the regression report](tests/results/answer-reference-shared-packet-regression.json).

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

Pending: qualification and production integration of the new bounded semantic planner/executor, full adaptive direct-context selection beyond current complete-document routes, complete shared tool/evidence contract, independent OCR and optional format processors, broader conversational-reference resolution beyond explicit answer points, and end-to-end attachment quality acceptance. Existing routing remains until the validated replacement is integrated.

The earlier combined regression passed **190 tests**, with zero failures/errors,
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
runtime hashes. The final run after the hydration and label tests passed 216 tests in 373.484
seconds, with no failures/errors and unchanged runtime source hashes. Results
are in tests/results/planner-tools-final-regression.json.

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

## Prior-answer reference implementation

Production follow-ups now resolve explicit ordinal points, bullets, items and
headings against a completed ancestor answer on the active branch. They do not
reinterpret answer point two as document section two. The original selected
section is retained when the prior operation had one.

Prior wording is an unverified topic, never new source evidence. Current selected
file identities and saved citation relationships are validated again. Missing
citations, changed documents and ambiguous structures fail safely. Exact
calculations/counts are not converted into approximate retrieval answers.

Thirteen focused tests passed, covering nested lists/code fences, ambiguity,
branch siblings, deleted citation relationships, explicit filenames, original
section scope and exact-operation safeguards. The runtime dependency is included
in run.py's existing explicit reload list.

The first live development probe used a controlled completed prior answer and
real installed embedding/chat models in an isolated PRE_ACTIVATION fixture. It
resolved the correct point, completed in 82.097 seconds and produced a relevant
restaurant analogy. It still omitted citations. Calls: one embedding, one
reranking and one final generation, with no rewriting/planning call. First
server emission was 39.147 seconds. Indexing took 3.824 seconds separately.
This single observation is not a storage comparison or general quality result.
See tests/results/answer-reference-live.json.

The direct-read optimization is implemented: valid saved citations are hydrated
from authoritative source rows, checked against the original section and current
turn revision, and packed through the same formatter as retrieved passages.
All required cited passages must fit or the existing retrieval fallback is used.
Seventeen focused tests passed, including prompt equivalence and an unavailable
semantic index with readable canonical citations. The pre-format-unification
full regression passed 231 tests in 313.485 seconds. The final shared-format
regression passed 233 tests in 317.925 seconds with unchanged runtime source hashes.

An initial direct-read observation reduced calls to one generation call, versus
one embedding, one reranking and one generation. Elapsed observations were
79.717 versus 82.097 seconds. It still omitted citations and confused producer/
consumer roles in its analogy. This is not a quality pass or a reliable speedup
claim. The prompt format was subsequently unified, and a paired comparison on
the same source identities was added to avoid that confound.

Current continuation file changes:
- RAG/answer_references.py: bounded answer-structure selection.
- RAG/operations.py: completed-branch lookup, citation validation and source hydration.
- RAG/document_agent.py: follow-up resolution, explicit unverified reference and direct-source acquisition.
- RAG/context_budget.py: shared all-or-nothing evidence-packing option.
- run.py: watches the new runtime module.
- tests/check_answer_references.py: structure, branch, authorization, budget and prompt-equivalence cases.
- tests/smoke_answer_reference.py: opt-in live probe and paired prompt fingerprints.
- README.md and IMPLEMENTATION_STATUS.md: usage, results and honest limitations.
- tests/results/answer-reference*.json: executed regression/live checkpoints; no reports claim full assistant acceptance.

No other stage, root README, tree_store.py, model, model context setting or
dependency was changed. No files were removed and no migration finalization
was performed.
