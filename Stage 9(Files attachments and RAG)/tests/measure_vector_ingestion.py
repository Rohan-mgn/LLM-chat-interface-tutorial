"""Fresh-process ingestion memory qualification using the frozen numeric corpus.

Includes synthetic vector construction, SQLite migration-source creation, Lance
ingestion and compaction. No Ollama calls. This is a conservative combined-process
measurement, not isolated database allocator accounting or answer latency.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]


def child(path,selected,total):
    import psutil
    import benchmark_vector_backend as benchmark
    process=psutil.Process()
    initial=process.memory_info().rss
    peak=[initial]
    done=threading.Event()
    def sample():
        while not done.wait(.005):
            peak[0]=max(peak[0],process.memory_info().rss)
    thread=threading.Thread(target=sample,daemon=True)
    thread.start()
    started=time.perf_counter()
    try:
        payload=benchmark.setup(path,selected,total)
    finally:
        peak[0]=max(peak[0],process.memory_info().rss)
        done.set()
        thread.join()
    return {"selected":selected,"total":total,"seconds":time.perf_counter()-started,
        "initial_rss_bytes":initial,"peak_rss_bytes":peak[0],
        "incremental_peak_mib":(peak[0]-initial)/1024**2,
        "vector_disk_bytes":sum(p.stat().st_size for p in (path/"index").rglob("*") if p.is_file()),
        "raw_text_float32_bytes":payload}


def main():
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument("--child",nargs=3)
    args=parser.parse_args()
    if args.child:
        print(json.dumps(child(Path(args.child[0]),int(args.child[1]),int(args.child[2]))))
        return
    root=ROOT/"tests"/".artifacts"/("vi-"+uuid4().hex[:8])
    frozen=json.loads((ROOT/"tests"/"vector_acceptance.json").read_text())
    results=[]
    for selected,total in ((100,100),(1000,1000),(4000,4000),(4000,40000)):
        path=root/(str(selected)+"-"+str(total))
        process=subprocess.run([sys.executable,"-B",__file__,"--child",str(path),str(selected),str(total)],
            capture_output=True,text=True,timeout=600)
        if process.returncode:
            raise RuntimeError(process.stderr[-8000:])
        row=json.loads(process.stdout)
        row["memory_passed"]=row["incremental_peak_mib"]<=frozen["peak_incremental_memory_mib"]
        row["disk_passed"]=row["vector_disk_bytes"]<=max(256*1024**2,4*row["raw_text_float32_bytes"])
        row["native_diagnostics"]=process.stderr[-8000:]
        results.append(row)
        print(json.dumps({k:row[k] for k in ("total","incremental_peak_mib","memory_passed","disk_passed")}),flush=True)
        report={"scope":__doc__,"python":sys.version,"corpus_seed":93218,"dimensions":768,
            "frozen_version":frozen["version"],"memory_limit_mib":frozen["peak_incremental_memory_mib"],
            "adapter_sha256":hashlib.sha256((ROOT/"RAG"/"vector_store.py").read_bytes()).hexdigest(),
            "setup_sha256":hashlib.sha256((ROOT/"tests"/"benchmark_vector_backend.py").read_bytes()).hexdigest(),
            "cases":results,"complete":len(results)==4,
            "passed":len(results)==4 and all(r["memory_passed"] and r["disk_passed"] for r in results),
            "limitations":"One ingestion run per corpus in a fresh process. 5ms RSS sampling can miss short peaks; includes baseline creation. No model memory."}
        (ROOT/"tests"/"results"/"vector-ingestion-memory.json").write_text(json.dumps(report,indent=2),encoding="utf-8")


if __name__=="__main__":
    main()
