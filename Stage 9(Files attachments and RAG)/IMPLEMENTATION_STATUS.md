# Stage 9 implementation status

This is an implementation checkpoint, not a completion claim. Only Stage 9 files are being changed.

## Baseline and current live state

- Original regression baseline: 104 tests, no failures/errors, 52.752 seconds.
- Native environment: Windows 11, Python 3.14.3, LanceDB 0.40.0.
- Model defaults and 16,384-token allocation are unchanged. No models were downloaded.
- Latest live status inspection: SQLite, PRE_ACTIVATION, epoch 0. No live cutover or cleanup has been performed.
- Tests and evaluations use isolated databases under tests/.artifacts.

## Milestone 1 - storage

Implemented and exercised:

- Dedicated Lance adapter with externally supplied float32 vectors, exact cosine search and authorization prefilters.
- SQLite-authoritative passage hydration, durable epochs/leases, source generations and publication manifests.
- Idempotent native writes, crash/acknowledgment recovery, deletion outbox and bounded reconciliation.
- Upload source records and pending build intent committed before embedding inference.
- Explicit compatible-digest repair from authoritative sources, with cancellation and source-change checks.
- Preparation, activation guards, current-data rollback, fixture finalization and retention-guarded artifact retirement.
- Recovery backups record the backend epoch, embedding spaces and authoritative generation-manifest identity.
- CLI status, prepare, validate, reconcile, activate, rollback, finalize, rebuild, maintenance and retire commands.
- Native runtime, populated migration, recovery, authorization and concurrency tests.
- Numeric search benchmarks: 100, 1,000, 4,000 and selective 4,000/40,000 chunks; 30 cold and 30 warm repetitions.
- Paired 40-case development evaluations with the existing local models.

Still pending:

- Final combined regression result after the latest reconciliation change.
- Completed paired held-out comparison and complete frozen acceptance review.
- Ingestion peak-memory qualification and remaining deployment/maintenance acceptance checks.
- Supported-worker rollout and validated live cutover.
- A real stabilization period of at least 14 days, explicit live finalization, schema verification and later explicit backup retirement.

SQLite FTS5 remains the lexical backend. A corrected 13-query Unicode/identifier/phrase fixture matched on 12 queries; Lance did not reproduce SQLite's accent folding for cafe/cafe-with-accent.

The full regression run before the last race fix passed 152 tests. The newest full run is in progress; tests/results/storage-full-regression.json records the last completed run. Do not interpret an in-progress evaluation JSON as a complete 40-case run.

## Milestone 2 - adaptive attachment assistant

The existing document tools, summaries and optional vision behavior remain. The newly requested parser registry, advanced planning/task state, shared evidence bundle, final-answer coverage mapping and cross-process source-aware SSE release are **not complete**.

This milestone has not been represented as finished based on retrieval benchmarks. New assistant capability development follows validated storage cutover.

## Measured limitations

- Development retrieval Hit@K/Recall@K/MRR/nDCG: 0.95238 for both backends (21 measured retrieval cases).
- Development median end-to-end latency: SQLite 13.340 seconds; Lance 16.351 seconds.
- Median model calls: 2 for both. No measured answer-latency improvement is claimed.
- Existing answer-term and abstention checks are heuristics, not semantic correctness proofs. Development abstention heuristic: 5/9 for each backend.
- Native Windows latest-version-hint rename warnings occurred despite passing manifest/visibility tests; this remains documented.
- Numerical benchmark search RSS excludes ingestion peak memory. The original cache_bytes field was measured after cache clearing and is invalid; the corrected script records SQLite cache memory before clearing.
- No proprietary-model equivalence claim or frontier-reference comparison: no reference outputs have been supplied.

See [VECTOR_STORAGE.md](VECTOR_STORAGE.md) for architecture, commands, measurements and safety boundaries.
