# Evaluation interpretation

The 80-case corpus is synthetic. Development and held-out document families are
disjoint; related task categories are intentionally represented in both splits.
Freeze implementation/settings before reading held-out scores. Record any later
bug fixes as a new evaluation version rather than silently retuning a holdout.

Offline model doubles test contracts, routing, exact tools and lexical fallback.
They do not measure real semantic embeddings, generated-answer accuracy or
groundedness. Live reports save answers for independent inspection.

## Reference metrics

- Retrieval metrics use relevant **files**, with binary relevance; repeated
  passages from one file count once. This is not fine-grained passage Recall@K.
- Route accuracy compares the executed route with the annotated route.
- Tool checks read persisted provenance, not generated Markdown.
- Reference-term presence is a screening heuristic, not full correctness.
- Citation validity checks identity/existence, not claim support.
- Abstention uses a disclosed phrase heuristic and requires manual review.
- p95 is reported only with at least 20 observations. Report categories with
  their sample counts; small categories cannot establish general performance.
- Vector benchmarks report synthetic cold/warm CPU behavior separately. Compare
  end-to-end runs only with matching corpus, queries, model digests, context
  settings and hardware; cold model loads and other concurrent work are confounds.

## Manual or optional evaluator rubric

Score each dimension 0, 1, or 2 using the saved answer and actual source passages:

| Dimension | 0 | 1 | 2 |
| --- | --- | --- | --- |
| Correctness | Wrong requested fact/result | Partially correct or materially incomplete | Correct requested answer, including qualifications |
| Relevance | Answers another question | Relevant with substantial irrelevant material | Directly answers the requested question |
| Groundedness | Important invented claims | Some claims lack support | Factual claims supported or explicitly qualified |
| Citation support | Citations unrelated to claims | Mixed support | Attributed excerpts support their claims |
| Citation coverage | Main document facts uncited | Some important facts uncited | Important document facts have appropriate citations |
| Abstention | Invents absent information | Unclear limitation | Clearly identifies missing/conflicting evidence |

Keep human/model evaluator identity and rubric version with scores. A model's
self-assessment or claim-review flag is advisory, not ground truth. No paid
evaluator or additional model is required or installed by these scripts.

## Baseline

package-baseline-live.txt records the unchanged, mechanically moved application's
original three-question live fixture. It is a small comparison baseline, not an
80-case pre-upgrade measurement. development-trial.json is an interrupted
development diagnostic, not a final comparison run. Do not combine it with
held-out scores or claim improvements from unlike runs.
