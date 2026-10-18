# Attachment capability and acceptance matrix

This matrix distinguishes implemented extraction/tool behavior from measured answer
quality. A format being accepted, a parser test passing, or a citation resolving
does not establish that the final answer is faithful or complete. The configured
`llama3.2:3b` answer model remains text-only with the 16,384-token allocation.

## Format capabilities

The model-free drawing additions and their current qualification are described in
[EVIDENCE_DRAWING_DELIVERY.md](EVIDENCE_DRAWING_DELIVERY.md). They provide native
coordinate-preserving inspection, ruled title-block/revision corroboration, bounded
reference matching, explicit dimensions, source-versioned reviews and guarded count
labels. No new model is enabled. The older optional vision-provider paths listed
below are historical configurable capabilities; they were not used for the new
drawing results and are not an implemented model-free OCR solution.

| Input | Native extraction | Tables / code structure | OCR and visual interpretation | Limitations |
| --- | --- | --- | --- | --- |
| TXT / Markdown | Paragraphs; Markdown headings | Text only | None | Plain text does not preserve page layout |
| PDF | Native text and page locations; bounded layout fallback | No verified PDF table reconstruction | Unreadable pages can use a configured vision provider and optional PDF renderer | Native page text does not imply figures were interpreted; encrypted PDFs rejected |
| DOCX | Paragraphs, heading styles and table-row text | Table text, not calculator-ready typed cells | No embedded-image interpretation path | No tracked-change/layout fidelity claim |
| CSV | Headers, rows and cell values | Deterministic aggregation, filters, comparisons and rankings | None | Unique headers required; numerical argument selection can fail |
| XLSX | Sheet/row/cell values, cached formula values | Same calculator as CSV | No workbook chart/image interpretation | No formula execution, macros, external links or recalculation |
| PPTX | Slide order, text, notes, table-row text, cached chart values and image metadata | No calculator-ready presentation tables | Embedded image references are explicitly uninterpreted | Cached chart values are not a visual reading of the chart |
| Python | Source lines and whitespace | AST symbols/imports; syntax errors retain raw source with warning | None | Uploaded code is never imported or executed |
| JSON / XML | Source lines plus bounded structural metadata | JSON root keys; safe XML parsing | None | XML entities rejected; malformed JSON remains text with a warning |
| JS / JSX / TS / TSX / CSS / YAML | Source lines and whitespace | No complete language-semantic parser | None | Explanations depend on the model's reading of source text |
| PNG / JPEG / WebP | Preview and image metadata | None | Optional configured vision transcription and a separately labeled description | No native text or independent OCR engine; no vision quality claim from mocked tests |
| Audio / video | Unsupported | Unsupported | Unsupported | No transcription, frame sampling or temporal analysis |
| Encrypted/password-protected files, legacy XLS/PPT/DOC, uncommon formats | Unsupported unless explicitly registered and tested | Unsupported | Unsupported | No extension-based fallback that claims successful parsing |

The optional image/PDF path currently asks a vision model for transcription,
description and uncertainty. It is not an independent OCR implementation. A
transcription and an image description remain separate block types. There is no
automatic model download or model switch. Native parser workers retain their
Windows memory, CPU, process-count and wall-time limits.

The authoritative format list is `RAG/parser_registry.py`. Extraction details are
in `RAG/document_parsers.py` and `RAG/structured_formats.py`; numerical behavior is
documented in [TABLE_COMPARISONS.md](TABLE_COMPARISONS.md).

## Scenario-level acceptance evidence

| Required scenario | Existing implementation / verification | Outstanding acceptance |
| --- | --- | --- |
| Explain a whole PDF to a child | Native PDF fixtures; direct complete-context path and request-preserving hierarchy | Live child lesson probes used Markdown, omitted citations and included factual errors; full PDF child-quality acceptance remains open |
| Chapter-by-chapter study guide | Coverage manifests, complete-context and hierarchical tests; live maintenance-guide probe | Source-section processing is not final-answer completeness; live citation/format quality has not passed |
| Targeted facts from large documents | Hybrid retrieval, scope filtering, bounded expansion; development and held-out retrieval reports | Retrieval scores and term-presence heuristics do not establish final-answer faithfulness |
| Cross-document contradiction analysis | Balanced scope and missing-document disclosure; comparison route tests | Independent assessment of substantive contradictions remains needed |
| Spreadsheet period/percentage comparisons | Exact arithmetic, source-row manifests and API tests | Interpreter baseline 0/5 development; experimental candidate 5/5 development, 3/6 new held-out; candidate not promoted |
| Presentation slide relationships | Ordered text/notes, slide reads and parser fixtures | Live relational explanation and figure interpretation not accepted |
| Source-code explanation and failure analysis | Nonexecuting Python AST and line tools; syntax-error/provenance tests | Live semantic explanation/failure-analysis quality remains unmeasured |
| Scanned PDF / mixed native and OCR | Optional vision-transcription path, cancellation/cache/concurrency fixtures | Independent OCR pending; real OCR accuracy and mixed-document quality not accepted |
| Image questions with/without vision | Preview, explicit unavailable capability, optional provider and mocked tests | No configured live-vision quality result in this continuation |
| Natural-language follow-up references | Completed active-branch answer-point resolution; current source validation; paired live probe | Paired probe omitted citations; broader pronoun/reference understanding remains incomplete |
| Requests needing no retrieval | Ordinary-chat bypass; direct complete documents and canonical cited follow-ups | Model quality remains distinct from skipped tool calls |
| Ambiguity / retrieval failure | Validated arguments, bounded tools, unavailable-index paths and clarification tests | Models can still select a valid but semantically wrong operation |
| Attachment prompt injection | Untrusted-data instructions, application-owned validators, scope/revision tests | Test coverage does not establish universal resistance |
| Unsupported formats / incomplete extraction | Registry rejection, extraction warnings and structural coverage diagnostics | No unimplemented format or unreadable material counts as covered |
| Source changes at output emission | Cross-process release gate, cancellation, bounded queue and race tests | Server-send accounting only; no browser acknowledgment protocol |

Relevant fixture suites include `check_answering.py`, `check_answer_references.py`,
`check_attachment_tools.py`, `check_table_comparisons.py`, `check_parser_registry.py`,
`check_document_intelligence.py`, and `check_evidence_release.py`. Final test counts
and errors are recorded in [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).

The frozen thresholds in `tests/attachment_acceptance.json` remain unchanged.
Final-answer quality, tool selection, citations, numerical semantics, latency,
tokens and resource use must be evaluated separately. The current evidence does
not establish frontier-model equivalence or complete either Stage 9 milestone.
Storage finalization additionally requires the retained live database and its
stabilization/finalization checks; this checkout has only historical cutover reports.
