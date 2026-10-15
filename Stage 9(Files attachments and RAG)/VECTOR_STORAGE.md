# Dedicated vector storage: implementation and qualification

The live application has passed cutover acceptance and now uses LanceDB in
CUTOVER_ACTIVE, epoch 1. All 63 existing sources across four files were transferred
and validated without re-embedding. Authoritative tables matched the verified
backup. Live finalization and retirement remain pending the minimum 14-day
stability/retention periods and explicit decisions. Fixture cleanup is not live
finalization. See tests/results/live-cutover.json.

## Resulting storage boundary

SQLite owns documents, canonical blocks, source text, permissions, branches and
citations. The new additive lifecycle tables store epochs, leases, generation
manifests and retryable operations, not embeddings.

RAG/vector_store.py is the only LanceDB client integration. It receives numeric
vectors from the existing provider and uses exact cosine search. It returns
candidate identities and scores without passage text. RAG/index_lifecycle.py
checks current SQLite ownership, source revisions and publication state and
hydrates passage text from SQLite. Existing lexical FTS5, RRF, reranking, adaptive
retrieval and evidence budgeting remain in place.

The default vector directory is DATA_DIR/vectors/lancedb. VECTOR_DB_DIR may
override it with an absolute path. Embedding spaces include the model digest,
dimensions, representation, input prefixes, normalization and cosine metric.
No model downloads or default database embedding functions are used.

## Candidate selection and measurements

LanceDB 0.40.0 passed the frozen cutover gates and is the selected backend. The native Windows
Python 3.14 tests exercise float32 vectors, exact filtered search, persistence,
upserts, deletion, FTS, process coordination and crash/retry boundaries.

A long generated table path initially failed in the Windows native library.
Short internal table names avoid that failure; the full embedding identity
remains in schema metadata and row filters. Deep user-specified paths still
need qualification on the target machine.

The isolated smoke run emitted a latest-version-hint rename warning during
repeated writes. The warning also recurred in the combined regression run; native manifest checks and adapter tests still passed. This is recorded
rather than treating a dependency-resolution dry run as sufficient validation.

Thirty process-cold and thirty warm searches were measured for each backend and
each corpus size, with identical 768-dimensional synthetic vectors and queries.

| Selected / total chunks | SQLite warm median | Lance warm median | SQLite process-cold median | Lance process-cold median |
| --- | ---: | ---: | ---: | ---: |
| 100 / 100 | 7.88 ms | 26.05 ms | 50.56 ms | 2342.78 ms |
| 1000 / 1000 | 149.60 ms | 31.23 ms | 587.83 ms | 2269.77 ms |
| 4000 / 4000 | 366.32 ms | 42.01 ms | 2217.02 ms | 2356.90 ms |
| 4000 / 40000 | 665.71 ms | 277.73 ms | 2390.08 ms | 3564.21 ms |

All measured top-30 lists agreed; maximum score difference was below 5.4e-8.
Lance warm p95 for the shared corpus was 423.23 ms; process-cold p95 was 5473.36 ms.
The largest measured incremental search RSS was 222.86 MiB. Shared-corpus Lance
disk usage was 254,808,205 bytes, including retained versions.

These are **numeric search adapter measurements**, not end-to-end RAG speedups.
Cold means a fresh Python process, not a flushed operating-system page cache.
The warm SQLite comparison includes its packed-vector cache. SQLite is faster
for the smallest corpus and has substantially lower import/startup overhead.
Some regression work overlapped this benchmark; timing is subject to system load.
Fresh-process ingestion memory was also measured. The original whole-generation
Python-list writer exceeded the 512 MiB limit: 574.887 MiB at 4,000 chunks and
574.027 MiB for the 40,000-chunk corpus. The revised writer streams 128-row Arrow
batches into one atomic merge and verifies full numeric manifests in batches.
Measured peaks fell to 289.926 MiB and 335.418 MiB respectively, below the unchanged
limit. An invalid last batch does not commit partial data; retries preserve row
counts and manifests. The retained baseline and new measurements are in
tests/results/vector-ingestion-baseline.json and vector-ingestion-memory.json.
These are 5ms-sampled RSS measurements including synthetic vector construction
and SQLite migration-source creation, not a mathematical memory bound. The original
benchmark's cache_bytes field is invalid because it was captured after clearing
the SQLite cache. The corrected script measures before clearing; its 100-vector
smoke run measured 329,118 bytes, including keys, dictionary and packed arrays.
This smoke run does not replace the 30-repetition benchmark.

Paired development evaluations include full retrieval, hydration and inference:
40 cases per backend, with identical corpus/model/settings fingerprints.
Both measured retrieval Hit@K/Recall@K/MRR/nDCG at 0.95238 across 21 retrieval cases,
and returned-evidence precision at 0.92857. Median end-to-end latency was 13.340 s
for SQLite and 16.351 s for Lance; median first-token latency was 10.140 s and
12.529 s respectively. Median model calls remained two. These variable-load
runs show no answer-latency improvement. The comparison script recomputes nDCG
against all expected files, so omitted evidence is not rewarded.
Answer-term and abstention checks remain weak heuristics; neither backend is
claimed to have achieved frontier-level factual reliability.

The reproducible script and raw observations are:
- tests/benchmark_vector_backend.py
- tests/vector_acceptance.json
- tests/results/vector-backend-benchmark.json

Chroma remains the principal alternative if Lance fails the complete frozen
acceptance gates. Its documented PersistentClient supports local persistence;
the current reference recommends server-backed instances for production.
Chroma was not installed or benchmarked, so no relative performance claim is
made. [Chroma client reference](https://docs.trychroma.com/reference/python)

Qdrant also has a local Python mode; a server is not mandatory for that mode.
There is no measured reason in this application to add a dedicated server.
Qdrant was not installed or benchmarked.
[Qdrant local mode](https://qdrant.tech/documentation/frameworks/langchain/)

Lance's native BM25 interface supports prefilters and positional search.
The adapter tests exercise phrase search and incremental/deletion visibility.
SQLite FTS5 is retained. The corrected 13-query compatibility fixture matched on
12 queries, including exact identifiers, punctuation, numbers, phrases, Cyrillic
and Chinese text. SQLite matched an unaccented query against accented text where
the configured Lance tokenizer did not. This small fixture is not proof of broad
language equivalence. Raw cosine and BM25 scores are not mixed.
[Lance full-text search](https://docs.lancedb.com/search/full-text-search)

## Publication, retry and fencing

Each native write uses a generation-specific row ID and real merge-upsert.
Repeated writes do not rely on primary-key metadata for uniqueness.
Publication verifies row identities, source fingerprints and vector manifests;
a newer table version alone is not sufficient.

Authoritative chunks and a durable pending build record are committed BEFORE
embedding inference. A second pending operation records the full embedding space
once its dimensions are known. Current source generation and operation metadata
are committed together with the corresponding authoritative updates. Publication uses a short SQLite transaction
after native verification. Metadata failure leaves the generation unpublished.
A retry can recover an already-written complete generation without re-embedding.

OS-backed short storage gates coordinate processes. Separate OS reservations
identify live operations; an expired SQL heartbeat does not prove the worker
has stopped. Each retrieval/index operation reads the durable backend epoch.
A changed epoch or invalid lease fails closed. Legacy SQLite embedding writes
are rejected by triggers after activation.

These protections cover updated workers. A first live rollout must also prove
that unmodified old workers have stopped; a successful fixture transition does
not establish that deployment condition.

File deletion writes a durable cleanup operation before authoritative rows
cascade away. Completed deletion tombstones are rechecked in bounded maintenance
batches so delayed native writes cannot permanently recreate deleted generations.
Empty generations are not rewritten merely to acknowledge deletion again. Bounded reconciliation retries vector deletion and incomplete
acknowledgments. Retrieval never uses deleted documents even while cleanup is
pending. Reconciliation does not download models or re-embed with a different
digest.

## Migration and recovery

RAG/vector_migration.py implements preparation, validation, guarded activation,
current-data rollback and explicit finalization. Backups are verified SQLite
snapshots outside active database directories. The CLI defaults to the ignored
Stage 9/.migration-backups directory; --backup-dir accepts a separate absolute path.

- PRE_ACTIVATION: legacy retrieval is active; compatible vectors can be copied.
- CUTOVER_ACTIVE: Lance is the sole semantic engine; ordinary rollback remains.
- FINALIZED: legacy vector column is gone; repair/rebuild is the default recovery.
- RETIRED: explicit retirement of named migration artifacts, with a new vector-free authoritative recovery snapshot retained.

Rollback restores current vectors without restoring an old application
database over new chats. Finalization requires a separate explicit decision and
at least 14 days of stabilization. Unknown schema dependencies cause cleanup to
stop safely; they are not dropped blindly. Fixture finalization verifies
foreign keys and reopens the resulting schema.

Do not invoke the internal lifecycle transition primitive as a migration command.
The manage_vectors.py CLI inspects supported launcher/ASGI workers before
activation, rollback, finalization, repair and retirement. This is not a claim
that arbitrary unregistered third-party workers have been fenced.

The rebuild command repairs one authorized document from canonical SQLite source
records. A healthy generation is reused; an interrupted complete native write
is recovered without embedding calls. Otherwise the exact recorded embedding
model digest, representation, prefixes and dimensions must be available.
Missing or changed models fail explicitly, preserving parsed document access.
Repair does not parse files again or change public source/citation identities.
Work is bounded to 4,000 sources and 30 minutes, with checks between batches.

Backups record the SQLite backend epoch, current generation-manifest digest and
embedding spaces. Recovery restores authoritative SQLite and verifies or rebuilds
the corresponding native generations. Retirement requires a separate decision,
at least 14 days of retention for EVERY migration backup, verified path
containment and a fresh recovery snapshot. Unlink/acknowledgment loss is retryable.
No live retirement or finalization has been performed.
There is no implemented post-finalization reverse migration to SQLite.

## Maintenance and retention

Compaction/index optimization is coordinated with adapter access. Without an
explicit cleanup decision the prune cutoff precedes every existing version,
and the adapter verifies those versions remain. Cleanup refuses protected
snapshots and enforces a retention minimum of 14 days. Elapsed time alone
does not authorize deletion.

## Development commands

From Stage 9 in PowerShell:

~~~powershell
..\.venv\Scripts\python.exe -m pip install -r requirements-vector-dev.txt
..\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'check_*.py'
..\.venv\Scripts\python.exe -B tests/benchmark_vector_backend.py
~~~

These tests use isolated databases under tests/.artifacts. They do not migrate
the live database. requirements-vector.txt pins the native-tested dependency
set; requirements-vector-dev.txt adds the test tools. Model defaults, context
allocation and the existing launcher remain unchanged.

## Remaining acceptance work

All frozen storage cutover gates passed; the live transition is recorded in
storage-cutover-acceptance.json and live-cutover.json. The pre-cutover full suite
passed 157 tests. Final live cleanup, restart schema verification and later
backup retirement are still pending their retention windows and explicit decisions.

The separate attachment milestone includes new parser and source-aware SSE work,
but end-to-end assistant quality is not established by storage benchmarks.
See IMPLEMENTATION_STATUS.md for current capabilities and unfinished work.

## Explicit operator commands

Run these from Stage 9 with the project virtual environment:

~~~powershell
..\.venv\Scripts\python.exe -B manage_vectors.py status
..\.venv\Scripts\python.exe -B manage_vectors.py validate
..\.venv\Scripts\python.exe -B manage_vectors.py reconcile
..\.venv\Scripts\python.exe -B manage_vectors.py maintenance
~~~

After validated cutover, stop Stage 9 before a one-document repair:

~~~powershell
..\.venv\Scripts\python.exe -B manage_vectors.py rebuild --conversation-id 1 --file-id YOUR_EXISTING_FILE_ID
~~~

Preparation copies compatible vectors and creates a verified backup; it does not
activate the backend. Activation requires a complete frozen acceptance report,
existing absolute artifact paths and an offline supported-worker rollout:

~~~powershell
..\.venv\Scripts\python.exe -B manage_vectors.py prepare
..\.venv\Scripts\python.exe -B manage_vectors.py activate --acceptance C:\path\to\validated-acceptance.json
~~~

Finalization and retirement remain separate operator decisions, not automatic
startup tasks. Their --decision flags do not waive stabilization or retention:

~~~powershell
..\.venv\Scripts\python.exe -B manage_vectors.py rollback --decision
..\.venv\Scripts\python.exe -B manage_vectors.py finalize --decision
..\.venv\Scripts\python.exe -B manage_vectors.py retire --decision
~~~

Preparation and activation have been executed and validated on the live database.
Finalization and retirement commands have not been executed on live data. Unknown legacy schema dependencies stop finalization safely and
require an inspected migration rather than blind deletion.

For paired evaluation, run evaluate_rag.py with --live, --backend sqlite or
lancedb, --split development or held_out, and a distinct --output path. Then use
tests/compare_storage_evaluations.py with the baseline and candidate JSON paths
and --output. It rejects partial runs, duplicate cases, changed models, changed
settings and incomparable corpora. Do not tune retrieval settings on held-out
answers.

## Held-out comparison

Both backends completed the same 40 held-out cases without changing the frozen
retrieval settings. Measured Hit@K/Recall@K/MRR/nDCG were 0.85 on 20 retrieval cases;
returned-evidence precision was 0.825. Exact tool and route checks matched.
Median end-to-end latency was 10.926 s (SQLite) and 12.219 s (Lance); median
first-token latency was 7.729 s and 8.020 s. Median model calls remained two.
All measured citation IDs were valid; this does not prove claim support.
The abstention heuristic passed 6/10 cases for each backend.

Results are synthetic, document-level judgments. Runs overlapped some regression
and ingestion work, so timings are subject to system load and are not causal
proof of backend speed differences. No held-out prompt/threshold tuning occurred.
The Arrow change affects ingestion and full-manifest verification; the timed
retrieval/answering path was not changed during these comparisons.
