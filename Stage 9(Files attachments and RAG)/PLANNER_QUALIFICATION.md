# Attachment planner implementation and qualification

The bounded planner and application tool bindings are implemented separately from
the production router. They are **not enabled for ordinary chat requests**.
A model returning valid JSON is not sufficient qualification.

## Components

- `RAG/planner.py`: versioned answer/tool/clarification envelope, structured and
  native decision methods, validation, one repair, four decisions, eight-tool
  ceiling, two-search ceiling, cancellation and shared operation deadline.
- `RAG/attachment_tools.py`: authorized per-turn file aliases; direct reads,
  hybrid search, table inspection/calculation, nonexecuting code inspection and
  complete-document processing through existing Stage 9 components.
- `RAG/summarizer.py`: optional call guard allows a tool session to retain its
  inference count after failed or cancelled attempts; cache hits do not consume
  new model calls.
- `tests/check_planner.py` and `tests/check_attachment_tools.py`: independent
  deterministic decisions and actual isolated SQLite/document fixtures.

The caller must capture an `EvidenceRevision` for the actual active user turn,
pass `AttachmentTools.validate_scope` to the planner, and retain that same
revision for final prompt construction, SSE release and response persistence.
Tools validate arguments again even if invoked without the planner.

Only supported selected aliases appear in tool schemas. A table capability
requires real parsed tables, and code inspection requires parsed code metadata.
Tool results are bounded and remain untrusted content. Failed or oversized
results are not published into the evidence ledger. Complete-document results
record source coverage without claiming final-answer semantic completeness.

Passage evidence and deterministic calculation provenance are separate. Exact
calculations use the existing validated table engine; formulas are not executed.
Calculation bindings cover aggregation, filtering, exact-group differences,
percentage changes, ratios and rankings. Production table requests and planner
bindings share this arithmetic engine; enabling these operations does not qualify
or enable the experimental planner. See [TABLE_COMPARISONS.md](TABLE_COMPARISONS.md).

## Live development evaluation

Run from Stage 9 with installed local models:

```powershell
..\.venv\Scripts\python.exe -B tests/qualify_planner.py --live --output tests/results/planner-development-new.json
```

The frozen fixture contains 20 new development scenarios, each attempted with
structured planning and native tools. It does not use the established held-out
families. Before the first run, minimum valid-decision rate was set to 95% and
minimum action/scope accuracy to 90%.

This first-decision probe uses a synthetic metadata-only tool catalog and never
executes a model-selected tool. It measures neither execution success nor unsafe
execution rate. Production argument validators can be stricter. Passing this
probe alone would still require actual tool-execution, multi-step, cancellation
and end-to-end answer-quality acceptance.

The report records original decisions, errors, timings and Ollama token counts.
No model grades its own output. A timeout counts as failure. Native and structured
calls are interleaved; one observation per scenario is not a stable performance
comparison. No model is installed or changed by the script.

## Remaining integration gates

Do not replace the production router until a candidate meets development
qualification using the actual executor schemas and representative multi-step
tasks. Then evaluate frozen settings on distinct held-out scenarios.

Final generation still needs a single validated evidence packet and citation
mapping, persisted completed-operation metadata, branch-aware answer-reference
resolution, and coverage checks. Planner tool notes are not a final user answer.
Do not present model planning as frontier-model equivalence.

Ordinary regression command:

```powershell
..\.venv\Scripts\python.exe -B -m unittest discover -s tests -p "check_*.py" -v
```

## Recorded first-decision results

tests/results/planner-development.json contains all 40 completed attempts:

| Method | Valid decisions | Correct action and scope | Median call time |
| --- | --- | --- | --- |
| Structured envelope | 19/20 | 10/20 | 17.529 s |
| Native tools | 8/20 | 7/20 | 19.261 s |

Neither method qualified. Errors included using retrieval for exact table tasks,
asking unnecessary clarifications, mishandling prior-answer references, and invalid
native arguments. The first structured attempt timed out. Some semantic choices
such as reading an entire code file may be useful, but did not match the frozen
expected strategy; results are not retrospectively relabeled as passes.

This was run while local development/regression activity also used the machine.
Timings are observations, not controlled latency comparisons. No tool executions
occurred in this probe, so an unsafe-execution rate cannot be inferred from it.
