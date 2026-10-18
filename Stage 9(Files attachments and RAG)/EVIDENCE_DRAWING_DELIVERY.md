# Evidence, drawings and adaptive presentation — implementation checkpoint

This is an incomplete Stage 9 implementation, not an acceptance or frontier-parity claim.
The application keeps llama3.2:3b, the 16,384-token allocation, existing embeddings,
SQLite FTS5/BM25 and LanceDB responsibilities. No live storage finalization occurred.
No new model was installed or used. Drawing analysis uses native PDF geometry,
PDFium rendering and conventional OpenCV matching.

## Three separately reviewable changes

1. **Evidence and answering:** `RAG/claim_gate.py`, `research.py`, changes to
   `document_agent.py`, `rag.py`, `evidence_release.py`, and `main.py`.
   Source-dependent drafts are buffered before visible output. Canonical passages
   are reloaded from SQLite; unsupported critical claims cause cited extractive
   fallback. Non-extractive prose that passes lexical checks is explicitly an
   interpretation, not independently verified semantics. Verified output uses the
   existing bounded SSE release/mutation gate. Ordinary chat still streams normally.
   Research uses the existing hybrid index, deterministic ranking features and a
   maximum of two retrieval rounds. Candidate acquisition coverage is distinct from
   final-answer coverage and semantic completeness.

2. **Drawing evidence and review:** `RAG/drawing_*.py`, `drawings.py`, additive
   schema/routes, and `static/drawings.{js,css}`. Native coordinates, original page
   geometry, transforms, characters, paths, style, title-block fields and original
   rendered regions are retained. Large analysis data is stored in hash-checked
   artifacts with deletion-outbox cleanup. Analysis/review revisions invalidate
   dependent chat release and saved tool evidence. Human review has initially hidden
   predictions, region-completion checks, manual symbol boxes and attestation.
   Automated processing cannot promote a count to Exact. Uploaded reference images
   remain conversation-scoped, and deleting a reference invalidates its template.
   Dimension, junction and connection corrections have immutable draft/published
   records and explicit self-declared human attestations. Reviewed process paths
   exclude signal edges and preserve unknown direction. Exact totals use the actual
   reviewed boxes, with immutable saved counts and matching verification revisions;
   later edits invalidate both the saved response and reuse of its Exact label.
   Drawing JSON and image responses are checked at actual ASGI release.

3. **Adaptive presentation:** `static/response-ui.{js,css}` and
   `tests/response_checks.js`. Prose remains in the main chat scroll. Code, structured
   code blocks and tables use responsive component scrolling, copy, keyboard focus,
   expand/collapse and preserved streaming scroll/selection state. Drawing controls
   are tested separately in `tests/drawing_checks.js`.

These are separate file/test groups in the working tree; no remote PR has been created.

## Measured results

- The original 253 tests were reproduced before application edits: 200 recorded
  successes before an interrupted process, then all 53 unreported tests rerun and
  passed. Runtime hashes were unchanged. This was not one uninterrupted run.
  See `tests/results/stage9-preimplementation-baseline.json`.
- Eight focused claim/research tests passed, including fabricated-number rejection
  before SSE, missing-citation fallback, preserving source conditions and disclosing
  requests excluded by the subquestion budget.
- Fourteen drawing tests passed at the current integrated checkpoint: real drawing
  identity/revision follow-up, synthetic ruled title blocks, rotated/scaled outlines,
  distinct internal marks, actual chat counts, scoped counts, transforms and abstention.
- Eleven existing output-release/security tests passed after two tests were updated
  for verification-before-streaming. Draft cancellation now emits nothing. The
  cross-process test still checks that deletion waits for a bounded send and blocks
  later output.
- Separate adaptive-response and drawing-review browser suites passed on desktop
  and mobile. They use mocked model responses to test browser/API behavior.
- Seven frozen real-upload development probes improved from 0/7 to 7/7 automated
  checks using the preserved baseline application and the same installed models.
  See `tests/results/drawing-chat-before.json`, `drawing-chat-after-v1.json`, and
  `drawing-chat-comparison.json`. These checks cover expected fields and qualified
  abstentions, not independently human-scored overall answer quality.
- All three supplied P&IDs were reprocessed in isolated workers: one physical PDF
  page each, native vector linework, no embedded raster images. The expected drawing
  numbers ended in 000007/000008/000009 and the selected revisions were 04/04/02.
  These are extraction results, not independent real-drawing annotation accuracy.
- The initial full integrated run completed 270 tests in 1657.420 seconds: 267 passed,
  3 failed, no errors. One completion/Stop lifecycle bug was fixed. Two retrieval
  assertions were updated for deterministic ranking and targeting missing files
  in round two, retaining the same authorization and two-round cap. All three
  corrected regressions passed focused checks. The subsequent full run passed all
  282 tests, with zero failures, errors or skips and unchanged runtime hashes
  (`tests/results/evidence-drawing-final-regression.json`).
- Five additional drawing safety tests passed: explicit table/annotation dimensions,
  unit conversion, artifact integrity/deletion, stale review invalidation, contradictory
  revision tables and invalid zero-count verification scope. Four annotation tests
  passed, including persisted Exact counts, stale labels, uploaded references,
  reviewed process paths and JSON release races.
- Independent raster landmarks passed at all four rotations with non-default CropBox
  and UserUnit, including high-resolution crops. Revision-table entries corroborate
  all three supplied title-block revisions; no maximum-number/date selector is used.
- A browser run timed out waiting for upload under concurrent test workloads. The
  saved database subsequently showed successful extraction. An unchanged-fixture
  rerun passed desktop and mobile, including keyboard region selection, crop rendering
  and correction drafts (`tests/results/drawing-browser-recheck.log`). The earlier
  timeout is retained, not counted as a pass.
- The four-object, two-page synthetic count probe recorded precision/recall/class
  accuracy 1.0 and count 4, labeled Estimated. This tiny development fixture does not
  establish real-drawing performance or independent count verification.
- A separately frozen rotated/scaled fixture found five targets across two pages
  and excluded one marked lookalike: precision/recall/class accuracy 1.0, mean IoU
  0.9999999, count 5, Estimated. These pages deliberately contain no text. The
  initial harness incorrectly demanded text-index readiness; the corrected harness
  verifies drawing chat independently while recording unavailable native text.
  Both attempts are retained. See `tests/results/drawing-rotated-symbols.json`.
- Five frozen probes on the supplied 74-page book scored 2/5 on the preserved
  baseline and 2/5 on the initial implementation. The latter exposed an author
  byline lost by fallback excerpt selection and an invalid extraction route for
  a conceptual contrast. An isolated correction scores 5/5, with required cited
  page groups improving from 2/5 to 5/5 and all 13 emitted citations resolving.
  All 104 affected regression tests passed before the exact qualified files were
  promoted. See `tests/results/answer-corrections-integration.json` and
  `tests/results/book-chat-comparison.json`. These are term/page checks, not blind
  human factuality or prose-quality scores. The same development probes informed
  the fixes; they are not an untouched held-out evaluation.
- On those book probes, observed median latency was 14.01 seconds before and 7.34
  seconds for the isolated correction. Reported Ollama tokens were 13,652 input /
  1,545 output before and 5,882 / 895 after. Failed baseline requests lack attributable
  call metrics, so these are not complete workload totals. QA peak RSS was not
  measured; enforced worker limits are not a substitute for observed peak memory.

The full 282-test run preceded the last two answering corrections; their 104-test
affected run includes two new cases. These results are not a single 284-test run.
The latest drawing browser suite passed on desktop/mobile, including vector-only
PDF selection and actual chat counting. The separate attachment-browser rerun now also passes on desktop/mobile
(`attachment-browser-final-recheck.log`). The earlier approval-service failure
and two concurrent fixture-server startup timeouts remain historical failed
attempts; sequential browser reruns passed. Adaptive response checks passed separately.

## Latest continuation

- Qualified byline preservation and conceptual-comparison routing are integrated.
  The production book rerun scores 5/5 versus baseline 2/5, with cited-page groups
  5/5 versus 2/5 and all 13 citations resolving. Observed median latency is 6.81
  seconds in the first integrated run versus 14.01; reported tokens are 5,882 input / 742 output. Baseline
  missing-call and small-development-set limitations still apply. See
  `tests/results/book-chat-integrated-comparison.json`. The final number-word
  guard rerun also passes 5/5, with median 6.13 seconds and the same citation
  coverage (`tests/results/book-chat-final-comparison.json`).
- Competing field instructions now enter the prompt before packing. The release
  gate independently reloads source passages and displays competing values with
  citations even if the model omits one. Explicit length/mass/time units are
  normalized for comparison; incompatible quantities remain unresolved. This
  does not prove semantic contradiction or matching object/revision scope.
- The review UI restores current full-page manual drafts explicitly, preserving
  boxes, completed regions and unresolved items. Predictions stay hidden and
  human attestation is reset. Source, analysis and template changes reject stale
  restoration. Desktop/mobile browser checks pass, including script-like source
  text rendered inertly. Multi-page and region-scoped draft restoration in the
  viewer is still unsupported.
- The new endpoint suite initially failed because identical fixture uploads were
  deduplicated; a separate conversation now tests isolation. The conflict test
  initially omitted mock call recording; after fixing the instrumentation all
  10 claim/research tests pass. The broader affected run completed 64 tests: 63
  passed and that same instrumentation assertion failed. Runtime hashes match
  the corrected 10-test rerun; no application change was needed for that failure.
- A further review reproduced unsupported English spelled-out numbers passing
  as interpretations. The gate now treats English number words and common
  quantity expressions as critical claims requiring source-exact support. Its
  new SSE regression passes. The final answering/reference run passes all 40
  tests with unchanged runtime hashes. See
  `tests/results/final-claim-answering-regression.json` and the combined audit
  `tests/results/stage9-continuation-verification.json`.
- Final supplied-P&ID probes pass 7/7 versus baseline 0/7. Median observed latency
  was 1.91 versus 8.54 seconds. These are deterministic field/qualified-abstention
  cases, not general engineering-symbol accuracy. See
  `tests/results/drawing-chat-integrated-comparison.json`.

## Capability matrix

| Capability | Current status | Boundary |
|---|---|---|
| Conversation/source authorization and stale release | Implemented, tested | Server-emitted bytes, not browser acknowledgement |
| Native PDF title-block identity/revision | Conditional, real chat tested | Ruled labels/columns plus structurally recognized revision tables; ambiguity remains unresolved |
| Native characters, paths, boxes and transforms | Implemented | Clipping/complexity limitations are explicit |
| Original overview/crop rendering | Implemented | Bounded PDFium workers; 2,048-pixel rendering budget |
| Vector-only PDF without readable text | Conditional, chat endpoint tested | Text-index failure does not establish drawing-analysis failure; unsupported geometry still abstains |
| Selected reference/template matching | Conditional | Rotation/scale search is bounded; no universal symbol classifier |
| Bow-tie vector profile | Conditional, synthetic tests | Distinguishing internal marks are excluded; plant convention requires review |
| Exact counts | Human-review path only | Requires exhaustive version-bound review; no real counts certified |
| Explicit dimensions and unit conversion | Conditional, endpoint tested | Native annotations/ruled-cell identity or explicit human-reviewed corrections |
| Geometric line tracing | Conditional | Automatic endpoint continuity is not engineering connectivity; published human-reviewed process edges support paths |
| Drawing citations/review | Implemented, browser tested | Symbols, dimensions, junctions, connections, keyboard regions and high-resolution crops; reviewer identity is self-declared |
| PDF passage navigation | Implemented | Correct physical page; older passages may lack precise region geometry |
| General RAG synthesis | Conditional | Existing model can still misinterpret; conservative fallback may be verbose |
| Prose/code/table adaptive scrolling | Implemented, browser tested | JSON uses the established code renderer |
| Neural OCR, new vision models, photographs | Unsupported | Excluded by the no-new-model constraint |
| CAD verification | Unsupported | No authoritative CAD importer |

## Unfinished acceptance requirements

Do not treat this checkpoint as full completion. Remaining work includes a complete
ingestion-time section/table/entity intermediary, richer neighbor and relation
retrieval, robust general mixed text/drawing synthesis, broader revision conventions,
arbitrary instrument/tag classification, general dimension-arrow association,
junction/bridge/process/signal conventions, comprehensive independent validation
reports and broader held-out QA/detection evaluation.
Automatic legend interpretation, general engineering-symbol classification,
interactive scale calibration, durable queued analysis/cancellation endpoints,
multi-page/region draft restoration and richer unresolved-item workflows, and verified long-summary
completeness also remain unfinished. Current analysis requests run bounded workers;
their stored run records are not a complete durable job scheduler.

There are no exhaustive independently human-reviewed annotations for the supplied
real P&IDs. Synthetic fixtures and algorithm agreement do not replace them. No real
Exact count, blind-human clarity score, broad summary-quality improvement, or
frontier-model equivalence is claimed. The remaining applicable acceptance criteria
must be exercised before the overall Stage 9 specification can be called complete.

Install the qualified deterministic dependencies with
`uv pip install --python ../.venv/Scripts/python.exe -r requirements-drawing.txt`.
This includes the existing vector/runtime pins and adds no model dependencies.
