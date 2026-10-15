"""Reproducible numeric-backend comparison; no model calls or live application data.

Cold = fresh Python process (OS page cache is NOT flushed). Warm = same process.
Measures SQLite JSON/packed-cache scan versus the candidate Lance adapter using
identical externally supplied vectors and filters. This is NOT answer-quality or
end-to-end answering latency; those remain separate acceptance gates.
"""
import argparse
from array import array
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from RAG.vector_store import EmbeddingSpace, GenerationScope, LanceVectorStore, normalized

SPACE = EmbeddingSpace("embeddinggemma:latest","synthetic-fixed-v1",768,"benchmark-v1","document:","query:")
CACHE = {}


def scopes(n):
    return [GenerationScope(1,"file-selected",1,hashlib.sha256(b"selected").hexdigest())]


def baseline(path, query):
    # Reproduces bounded SQLite loads and packed process-cache cosine scan.
    with sqlite3.connect(path/"baseline.db") as db:
        rows = db.execute("SELECT id,vector FROM vectors WHERE conversation_id=1 AND file_id='file-selected'").fetchall()
    ranking=[]
    for sid, raw in rows:
        if sid not in CACHE:
            values=json.loads(raw)
            norm=math.hypot(*values)
            CACHE[sid]=array("f",(v/norm for v in values))
        ranking.append((sid,sum(a*b for a,b in zip(query,CACHE[sid]))))
    return sorted(ranking,key=lambda r:(-r[1],r[0]))[:30]


def measure(path, backend, query):
    import psutil
    process=psutil.Process()
    start_rss=process.memory_info().rss
    peak=[start_rss]
    finished=threading.Event()
    def sample():
        while not finished.wait(.005):
            peak[0]=max(peak[0],process.memory_info().rss)
    thread=threading.Thread(target=sample,daemon=True);thread.start()
    start=time.perf_counter()
    try:
        if backend=="sqlite":
            ranking=baseline(path,query)
        else:
            rows=LanceVectorStore(path/"index").search_vectors(SPACE,scopes(0),query)
            ranking=[(r["source_id"],r["score"]) for r in rows]
        elapsed=1000*(time.perf_counter()-start)
    finally:
        peak[0]=max(peak[0],process.memory_info().rss)
        finished.set();thread.join()
    return {"ms":elapsed,"incremental_peak_bytes":max(0,peak[0]-start_rss),
            "rss_bytes":peak[0],"ranking":ranking}


def setup(path,selected,total):
    import numpy as np
    rng=np.random.default_rng(93218)
    path.mkdir(parents=True)
    store=LanceVectorStore(path/"index");store.initialize_index(SPACE)
    with sqlite3.connect(path/"baseline.db") as db:
        db.execute("CREATE TABLE vectors(id TEXT PRIMARY KEY,conversation_id INTEGER,file_id TEXT,vector TEXT)")
        db.execute("CREATE INDEX vectors_scope ON vectors(conversation_id,file_id)")
        count=0
        file_number=0
        raw_size=0
        while count<total:
            n=selected if count==0 else min(1000,total-count)
            scope=scopes(0)[0] if count==0 else GenerationScope(file_number+1,"file-"+str(file_number),1,hashlib.sha256(str(file_number).encode()).hexdigest())
            vectors=rng.normal(size=(n,SPACE.dimensions))
            vectors/=np.linalg.norm(vectors,axis=1)[:,None]
            chunks=[]
            for i,vector in enumerate(vectors):
                sid="SOURCE_"+str(count+i)
                text="Synthetic technical manual item "+str(count+i)+": battery controller requires inspection."
                chunks.append({"id":sid,"text":text,"metadata":{"page":i//10+1},"chunk_index":i,"vector":vector.tolist()})
                db.execute("INSERT INTO vectors VALUES(?,?,?,?)",(sid,scope.conversation_id,scope.file_id,json.dumps(vector.tolist())))
                raw_size+=len(text.encode())+4*SPACE.dimensions
            store.upsert_chunks(SPACE,scope,chunks)
            count+=n;file_number+=1
    store.maintenance(SPACE)
    return raw_size


def summarize(rows):
    values=sorted(r["ms"] for r in rows)
    return {"repetitions":len(rows),"median_ms":statistics.median(values),
        "p95_ms":values[math.ceil(.95*len(values))-1],
        "max_incremental_peak_mib":max(r["incremental_peak_bytes"] for r in rows)/1024**2,
        "max_rss_mib":max(r["rss_bytes"] for r in rows)/1024**2}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--child",nargs=2)
    parser.add_argument("--repetitions",type=int,default=30)
    parser.add_argument("--quick",action="store_true")
    args=parser.parse_args()
    if args.child:
        path=Path(args.child[0])
        query=json.loads(sys.stdin.read())
        print(json.dumps(measure(path,args.child[1],query)))
        return
    import numpy as np
    repetitions=3 if args.quick else args.repetitions
    if repetitions<30 and not args.quick:
        parser.error("Acceptance runs require at least 30 repetitions")
    runs=[(100,100)] if args.quick else [(100,100),(1000,1000),(4000,4000),(4000,40000)]
    artifacts=ROOT/"tests"/".artifacts"/("vb-"+uuid4().hex[:8])
    report={"format_version":2,"scope":__doc__,"quick_smoke":args.quick,"python":sys.version,"cases":[]}
    for selected,total in runs:
        path=artifacts/(str(selected)+"-"+str(total))
        payload=setup(path,selected,total)
        rng=np.random.default_rng(1840)
        queries=[normalized(rng.normal(size=SPACE.dimensions).tolist(),SPACE.dimensions) for _ in range(repetitions)]
        case={"selected":selected,"total":total,"metrics":{},"quality":[]}
        cold={}
        warm={}
        for backend in ("sqlite","lance"):
            cold[backend]=[]
            warm[backend]=[]
            CACHE.clear()
            for i,q in enumerate(queries):
                child=subprocess.run([sys.executable,"-B",__file__,"--child",str(path),backend],
                    input=json.dumps(q),capture_output=True,text=True,timeout=60)
                if child.returncode:
                    raise RuntimeError(child.stderr)
                cold[backend].append(json.loads(child.stdout))
                warm[backend].append(measure(path,backend,q))
                if (i+1)%10==0 or args.quick:
                    print(f"{selected}/{total} {backend} {i+1}/{repetitions}",flush=True)
            case["metrics"][backend]={"cold":summarize(cold[backend]),"warm":summarize(warm[backend])}
            if backend=="sqlite":
                # Capture before clearing for the Lance run; arrays include their
                # allocated float32 buffers in getsizeof on this Python runtime.
                case["sqlite_cache_bytes"]=sum(sys.getsizeof(k)+sys.getsizeof(v) for k,v in CACHE.items())+sys.getsizeof(CACHE)
                case["sqlite_cached_vectors"]=len(CACHE)
        for temperature,left,right in [(label,a,b) for label,measurements in (("warm",warm),("cold",cold)) for a,b in zip(measurements["sqlite"],measurements["lance"])]:
            a=dict(left["ranking"]);b=dict(right["ranking"])
            overlap=len(a.keys()&b.keys())/min(30,selected)
            # Rank-sensitive agreement for untied synthetic vectors.
            relevance={sid:30-rank for rank,(sid,_) in enumerate(left["ranking"])}
            ideal=sum((2**score-1)/math.log2(i+2) for i,score in enumerate(relevance.values()))
            ndcg=sum((2**relevance.get(sid,0)-1)/math.log2(i+2) for i,(sid,_) in enumerate(right["ranking"]))/ideal
            case["quality"].append({"temperature":temperature,"top30_overlap":overlap,"ndcg":ndcg,
                "max_score_error":max(abs(a[sid]-b[sid]) for sid in a.keys()&b.keys())})
        case["disk_bytes"]={"sqlite":(path/"baseline.db").stat().st_size,
            "lance":sum(p.stat().st_size for p in (path/"index").rglob("*") if p.is_file()),
            "raw_text_float32":payload}
        report["cases"].append(case)
        output=ROOT/"tests"/"results"/("vector-backend-smoke.json" if args.quick else "vector-backend-benchmark.json")
        output.write_text(json.dumps(report,indent=2),encoding="utf-8")
        print(f"Recorded {selected}/{total}: {output}",flush=True)


if __name__=="__main__":
    main()
