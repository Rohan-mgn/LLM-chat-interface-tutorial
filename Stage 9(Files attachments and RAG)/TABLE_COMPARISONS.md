# Deterministic table comparisons

Stage 9 can calculate changes between explicit groups and rank grouped values
from parsed tables. The existing production table route and experimental planner
bindings use the same engine. Models interpret the requested columns, filters and
groups; application code performs arithmetic and renders the result. No additional
model generates or rewrites verified numerical results.

For example, a CSV with `Period,Amount` rows `Q1,100` and `Q2,125` supports
"What is the percentage change in Amount from Q1 to Q2?" A valid operation computes
a difference of 25 and a percentage change of 25%. A comparison within one table
does not require two attachments. Natural-language interpretation can still fail;
the development probe records those failures separately from arithmetic tests.

## Contract and boundaries

The existing `table`, `group_by`, `filters` and `aggregates` fields remain valid.
Two optional fields extend the contract:

```json
{
  "table": "CSV",
  "group_by": ["Period"],
  "filters": [],
  "aggregates": [{"op": "sum", "column": "Amount"}],
  "comparisons": [{
    "aggregate": 0,
    "baseline": {"Period": "Q1"},
    "current": {"Period": "Q2"},
    "metrics": ["difference", "percent_change", "ratio"]
  }],
  "ranking": {"aggregate": 0, "direction": "descending", "limit": 1}
}
```

Selectors must name every group column and match its value exactly. Aggregate
indexes are zero-based. Up to eight comparisons and 200 ranked groups are allowed
within existing table limits. Rankings use competition ranks and include every tie
at the requested boundary. No uploaded expressions, formulas or code are executed.

- Difference: current minus baseline.
- Percentage change: difference divided by the signed baseline, multiplied by 100.
- Ratio: current divided by baseline.
- Zero baseline: percentage and ratio are undefined; the difference is still valid.
- Missing group: unavailable, never treated as zero.
- Missing/non-numeric cells: original aggregate exclusions remain visible, but
  comparisons/rankings of those incomplete numerical aggregates are unavailable.
  Counts retain their explicit nonempty-cell semantics.
- Repeating decimal results: six decimal places, half-even rounding, with an exact
  numerator/denominator and a rounding flag in tool evidence. Average comparisons
  use exact fractions before rounding.

Each aggregate records row count, up to 200 source-row identifiers and a hash of
the complete ordered identifier list. Existing tool evidence binds the file
fingerprint, operation arguments, examined columns and formula-cache details.
Source revisions are revalidated by the existing tool and SSE release boundaries.
Answers show the applied filters and matched-versus-examined row counts explicitly.
This makes a model-selected subset visible without requiring the user to open the
tool-evidence panel; it does not prove the model selected the right subset.

The implementation does not infer why spending changed. Percentage-point changes,
currency conversion, joins, cross-file combined calculations and arbitrary formulas
remain unsupported. Multiple selected files are calculated independently. Table
extraction does not establish the accuracy or freshness of spreadsheet formula
caches. The renderer explains its formula and numerical limits.

The model's selected arguments can still be semantically wrong even when valid.
The production interpreter rejects a missing comparison for an explicit percentage
change rather than silently displaying a total. General planner rollout still
requires the unchanged qualification thresholds.

## Verification

The final full regression passed 253 tests with zero failures, errors or skips and
unchanged runtime source hashes. The focused comparison suite passed 17 tests;
schema rollback/retry has three additional tests. Reload checks also passed.
See [the final regression report](tests/results/table-comparisons-final-regression.json).

From Stage 9:

```powershell
..\.venv\Scripts\python.exe -B -m unittest discover -s tests -p check_table_comparisons.py -v
..\.venv\Scripts\python.exe -B -m unittest discover -s tests -p check_schema_initialization.py -v
..\.venv\Scripts\python.exe -B tests/run_regression.py --output tests/results/table-comparisons-regression.json
..\.venv\Scripts\python.exe -B tests/smoke_table_comparisons.py --live --output tests/results/table-comparisons-live.json
```

The live probe has five development cases with fixed expected results: period
change, department ranking, zero baseline, a region filter and a missing period.
It uses isolated ingestion fixtures and the real production chat path afterward.
It records final output, evidence, call/token diagnostics and latency. It is not
held-out qualification or proof of general attachment answer quality.

Schema initialization now groups document, lexical and vector metadata creation
into short transactions. Failure-injection tests check rollback and retry,
including a failure after vector metadata creation but before write guards.
No transaction spans inference or streaming.

Public design reference: [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling)
describes models proposing tool calls and applications executing them. This
implementation applies that separation with local models; it does not reproduce
private frontier-model internals or establish equivalent intelligence.

## Live interpretation results

The initial development run passed 0/5. Three requests hit the unchanged 25-second
interpretation deadline while Ollama was still loading the configured model; server
logs recorded cancellation during loading. The other requests failed interpretation.
A subsequent warm run also passed 0/5, demonstrating an interpretation problem
independent of cold loading. Reports retain both sets of failures.

A development-only candidate required explicit `comparisons` and `ranking` fields
and supplied compact grouping/aggregation examples. Version 1 passed 4/5; version 2
passed 5/5 after a clarification about filter columns versus group columns.
Version 2 was then frozen and evaluated on six separate cases. It passed **3/6**:
ties, average comparison and signed negative baseline. It failed renamed monthly
groups, a subset comparison and incomplete numeric data interpretation. The
candidate is **not adopted in production**. No held-out failures were used to tune
it afterward. The general planner remains separately unqualified and disabled.
In the incomplete-data holdout, the candidate invented a filter excluding the
unknown cell. The subsequent renderer disclosure is a general transparency fix,
not a prompt change or a reclassification of that failed holdout as a pass.

Artifacts: `tests/results/table-comparisons-live.json`,
`table-comparisons-warm-live.json`, `table-comparisons-candidate-live.json`,
`table-comparisons-candidate-v2-live.json`, and `table-comparisons-heldout-live.json`.
The held-out fixture is `tests/table_comparisons_heldout.json`; candidate execution
is opt-in via `--candidate`. The grading checks selected deterministic results,
not every aspect of semantic argument correctness or answer quality. Regression
activity overlapped these observations, so latency is not a controlled benchmark.

Validated arithmetic does not guarantee correct interpretation. The source
arguments and exclusions are shown in tool evidence so a model-selected operation
can be inspected. General attachment-quality acceptance remains unfinished.
