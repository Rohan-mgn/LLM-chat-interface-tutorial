"""Build storage acceptance from completed, reproducible local artifacts."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from manage_vectors import require_offline


def main():
    results=ROOT/"tests"/"results"
    artifacts=[]
    def read(name):
        path=results/name
        artifacts.append(str(path.resolve()))
        return json.loads(path.read_text(encoding="utf-8"))
    frozen=json.loads((ROOT/"tests"/"vector_acceptance.json").read_text())
    runtime=read("lancedb-qualification.json")
    regression=read("storage-full-regression.json")
    development=read("storage-development-comparison.json")
    held=read("storage-held-out-comparison.json")
    benchmark=read("vector-backend-benchmark.json")
    ingestion=read("vector-ingestion-memory.json")
    log_path=results/"storage-full-regression.log"
    log=log_path.read_text(encoding="utf-8")
    artifacts.append(str(log_path.resolve()))
    def exercised(*names):
        return all(name+" (" in log for name in names)
    runtime_ok=len(runtime["checks"])>=7 and all(check["passed"] is True for check in runtime["checks"])
    mismatches={}
    for name,version in runtime["versions"].items():
        if name!="pip" and importlib.metadata.version(name)!=version:
            mismatches[name]={"qualified":version,"installed":importlib.metadata.version(name)}
    runtime_ok=runtime_ok and not mismatches
    regression_ok=(regression["passed"] and regression["tests"]>=104 and
        regression["failures"]==regression["errors"]==regression["skipped"]==0)
    performance=not benchmark["quick_smoke"] and len(benchmark["cases"])==4 and ingestion["complete"] and ingestion["passed"]
    for case in benchmark["cases"]:
        for temperature in ("warm","cold"):
            measured=case["metrics"]["lance"][temperature]
            performance=performance and measured["repetitions"]>=frozen["repetitions"]
            performance=performance and measured["median_ms"]<=frozen[temperature+"_median_ms"] and measured["p95_ms"]<=frozen[temperature+"_p95_ms"]
            performance=performance and measured["max_incremental_peak_mib"]<=frozen["peak_incremental_memory_mib"]
        performance=performance and case["disk_bytes"]["lance"]<=max(256*1024**2,4*case["disk_bytes"]["raw_text_float32"])
        for quality in case["quality"]:
            performance=performance and quality["top30_overlap"]>=frozen["ranking"]["top30_overlap"]
            performance=performance and quality["ndcg"]>=frozen["ranking"]["tie_aware_ndcg"] and quality["max_score_error"]<=frozen["ranking"]["score_tolerance"]
    performance=performance and len(ingestion["cases"])==4 and all(row["memory_passed"] and row["disk_passed"] for row in ingestion["cases"])
    offline=False
    try:
        require_offline(ROOT)
        offline=True
    except ValueError as error:
        print(str(error))
    checks={
        "runtime":bool(runtime_ok),
        "regression":bool(regression_ok),
        "populated_migration":regression_ok and exercised("test_preparation_retries_preserve_authoritative_records","test_rollback_preserves_post_cutover_messages","test_finalization_time_decision_and_schema_guards"),
        "authorization":regression_ok and exercised("test_supplied_cosine_identity_only_and_authorized_prefilter","test_cross_conversation_file_cannot_attach_or_retrieve"),
        "citations":regression_ok and exercised("test_missing_index_rebuild_and_restart_keep_citations_and_source_ids","test_delete_creates_outbox_and_prevents_old_candidate_hydration"),
        "recovery":regression_ok and exercised("test_write_acknowledgment_loss_restart_is_recoverable","test_unavailable_digest_preserves_sources_and_does_not_embed","test_retirement_recovers_unlink_acknowledgment_loss","test_late_native_write_after_delete_ack_is_reclaimed"),
        "publication":regression_ok and exercised("test_1000_publication_compare_and_swap_interleavings","test_reader_observes_new_generations_from_another_process","test_metadata_failure_does_not_publish","test_multibatch_merge_is_atomic_and_retry_does_not_duplicate"),
        "retrieval_quality":development["passed"] and held["passed"] and development["paired_cases"]==held["paired_cases"]==40,
        "performance":bool(performance),
        "offline_worker_rollout":offline,
    }
    report={"frozen_version":frozen["version"],"created_at":time.time(),"checks":checks,"artifacts":artifacts,
        "dependency_mismatches":mismatches,
        "artifact_hashes":{p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in artifacts},
        "source_hashes":{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [*sorted((ROOT/"RAG").glob("*.py")),ROOT/"main.py",ROOT/"manage_vectors.py"]},
        "limitations":[
            "Storage qualification only, not advanced assistant or SSE revocation acceptance.",
            "1,000 SQL publication race checks and 120 actual cross-process generation writes are separate tests.",
            "Numeric search latency predates the ingestion-only Arrow optimization; retrieval query code is unchanged.",
            "Cold processes do not flush the OS page cache; timings vary with other system work.",
            "SQLite FTS5 is retained because native lexical equivalence was not established.",
            "Ingestion RSS is sampled, not a hard allocator bound.",
            "Live preparation, source validation and offline-worker recheck still run at activation."
        ]}
    (results/"storage-cutover-acceptance.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps({"checks":checks,"dependency_mismatches":mismatches},indent=2))
    return 0 if all(checks.values()) else 1


if __name__=="__main__":
    raise SystemExit(main())
