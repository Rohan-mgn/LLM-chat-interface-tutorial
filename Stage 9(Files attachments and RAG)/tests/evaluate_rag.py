"""Offline contract/lexical evaluation or optional real Ollama evaluation.
Offline model text is a fixture: never score it as real answer quality.
"""
import argparse
import asyncio
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import statistics
import sys
import time
from unittest.mock import patch
from uuid import UUID
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import check_rag as fixtures
from RAG import evidence

HERE=Path(__file__).resolve().parent

def metrics(rows):
    out={"cases":len(rows)}
    for key in ("hit_at_k","recall_at_k","mrr","ndcg","route_correct","tool_correct","answer_fact_terms","citation_validity","abstention_correct"):
        values=[r[key] for r in rows if r.get(key) is not None]
        out[key]={"mean":sum(values)/len(values) if values else None,"measured_cases":len(values)}
    for key in ("total_ms","ttft_ms","retrieval_ms","model_ms","model_calls"):
        values=sorted(r[key] for r in rows if r.get(key) is not None)
        out[key]={"median":statistics.median(values) if values else None,
                  "p95":values[math.ceil(.95*len(values))-1] if len(values)>=20 else None}
    return out

def numeric_match(value,expected):
    try:
        return abs(Decimal(value)-Decimal(expected))<=Decimal("0.001") if "." in expected else Decimal(value)==Decimal(expected)
    except InvalidOperation:return False

def main():
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument("--live",action="store_true")
    parser.add_argument("--split",choices=("development","held_out","all"),default="development")
    parser.add_argument("--limit",type=int,default=80)
    parser.add_argument("--family",help="Run a named development family for a targeted refinement check")
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    corpus=json.loads((HERE/"evaluation_cases.json").read_text(encoding="utf-8"))
    model_snapshot={}
    implementation_hash=hashlib.sha256(b"".join(p.read_bytes() for p in sorted((HERE.parent/"RAG").glob("*.py")))).hexdigest()
    if args.live:
        import ollama
        client=ollama.Client(timeout=5)
        try:
            model_snapshot={m["model"]:m["digest"] for m in client.list()["models"]}
            installed=set(model_snapshot)
            from RAG.model_provider import CHAT_MODEL,EMBED_MODEL
            missing={CHAT_MODEL,EMBED_MODEL}-installed
            if missing:raise RuntimeError("Required installed models unavailable: "+", ".join(missing))
        except Exception as error:
            print("LIVE EVALUATION NOT RUN: "+str(error));return 2
        finally:client.close()
    results=[];started=time.perf_counter()
    for family in corpus["families"]:
        if args.split!="all" and family["split"]!=args.split:continue
        if args.family and family["family"]!=args.family:continue
        if len(results)>=args.limit:break
        fixture=fixtures.Checks();fixture.setUp()
        try:
            if args.live:fixture.mock.stop()
            mapping={}
            counter=iter(range(1,10000))
            seed=int(hashlib.sha256(family["family"].encode()).hexdigest()[:24],16)
            file_ids_patch=patch("RAG.documents.uuid4",side_effect=lambda:UUID(int=seed+next(counter)))
            file_ids_patch.start()
            for file in family["files"]:
                f=fixture.upload(file["name"],file["text"].encode(),file["mime"],ready=False)
                for _ in range(1200):
                    state=fixture.app.documents.row(fixture.cid,f["id"])
                    if state["state"] in ("ready","failed"):break
                    time.sleep(.1)
                if state["parse_state"] not in ("ready","partial"):raise AssertionError(state)
                mapping[file["name"]]=f["id"]
            file_ids_patch.stop()
            inverse={v:k for k,v in mapping.items()}
            for case in family["cases"]:
                if len(results)>=args.limit:break
                before=time.perf_counter()
                if args.live:
                    events=fixture.send(case["question"],use_files=True,file_ids=list(mapping.values()))
                else:
                    # Deliberately disable query embeddings: measures real lexical fallback,
                    # routing, exact tools and API contracts without claiming semantic quality.
                    with patch.object(fixture.app.rag,"retrieve_dense",side_effect=RuntimeError("offline lexical evaluation")):
                        events=fixture.send(case["question"],use_files=True,file_ids=list(mapping.values()))
                elapsed=(time.perf_counter()-before)*1000
                row=fixture.rows()[-1]
                with closing(fixture.app.get_connection()) as db:
                    saved=db.execute("SELECT details FROM rag_runs WHERE message_id=?",(row["id"],)).fetchone()
                    tool_records=[json.loads(r[0]) for r in db.execute("SELECT data FROM tool_runs WHERE message_id=?",(row["id"],))]
                debug=json.loads(saved[0]) if saved else {}
                ranked=list(dict.fromkeys(inverse[s["file_id"]] for s in debug.get("sources",[]) if s["file_id"] in inverse))
                expected=set(case["relevant_files"])
                hit=[name in expected for name in ranked]
                dcg=sum(int(h)/math.log2(i+2) for i,h in enumerate(hit))
                ideal=sum(1/math.log2(i+2) for i in range(min(len(expected),len(ranked))))
                is_tool=case["route"] in ("TABLE_ANALYSIS","COUNT","EXACT_SEARCH")
                response=row["content"]
                tool_values=[]
                for tool in tool_records:
                    data=tool["exact_result"]
                    if "count" in data:tool_values.append(str(data["count"]))
                    for group in data.get("results",[]):
                        tool_values.extend(str(v["result"]) for v in group["values"])
                cited=re.findall(r"\[(SOURCE_[^\]]+)\]",response)
                valid={s["id"] for s in row["sources"]}
                absent=bool(re.search(r"couldn.t find|not (?:specified|provided|available|mentioned)|do not (?:provide|contain)|no (?:information|evidence)|missing|cannot determine",response,re.I))
                record={"id":case["id"],"family":family["family"],"split":family["split"],"category":case["category"],
                    "status":events[-1]["status"],"route":debug.get("route"),"route_correct":int(debug.get("route")==case["route"]),
                    "expected_files":sorted(expected),"ranked_files":ranked,
                    "hit_at_k":int(any(hit)) if expected and not is_tool else None,
                    "recall_at_k":len(set(ranked)&expected)/len(expected) if expected and not is_tool else None,
                    "mrr":next((1/(i+1) for i,v in enumerate(hit) if v),0) if expected and not is_tool else None,
                    "ndcg":dcg/ideal if ideal else (0 if expected and not is_tool else None),
                    "tool_correct":int(events[-1]["status"]=="completed" and all(any(numeric_match(v,term) for v in tool_values) for term in case["answer_terms"])) if is_tool else None,
                    "answer_fact_terms":int(all(term.casefold() in response.casefold() for term in case["answer_terms"])) if args.live and case["answer_terms"] else None,
                    "citation_validity":sum(s in valid for s in cited)/len(cited) if cited and args.live else None,
                    "abstention_correct":int(absent) if args.live and case["unanswerable"] else None,
                    "evidence_abstained":not ranked if case["unanswerable"] else None,
                    "total_ms":round(elapsed,2),"ttft_ms":debug.get("time_to_first_token_ms"),
                    "retrieval_ms":sum(debug.get("timings_ms",{}).get(k,0) for k in ("lexical_search","dense_search","reranking")),
                    "model_ms":debug.get("timings_ms",{}).get("model_inference"),
                    "model_calls":debug.get("model_call_count"),"metrics":debug.get("timings_ms",{}),
                    "answer":response if args.live or is_tool else None,
                    "errors":[e.get("message") for e in events if e["type"]=="error"]}
                results.append(record)
                print(json.dumps({k:record[k] for k in ("id","status","route_correct","total_ms")}),flush=True)
                groups=defaultdict(list)
                for result in results:groups[result["category"]].append(result)
                report={"live":args.live,"model_digests_at_start":model_snapshot,"implementation_fingerprint":implementation_hash,
                    "corpus_fingerprint":hashlib.sha256((HERE/"evaluation_cases.json").read_bytes()).hexdigest(),
                    "context_tokens":__import__("RAG.context_budget",fromlist=["CONTEXT_TOKENS"]).CONTEXT_TOKENS,
                    "claim_review_mode":os.getenv("RAG_CLAIM_REVIEW","off"),"processor":platform.processor(),"settings_fingerprint":hashlib.sha256(Path(evidence.__file__).read_bytes()).hexdigest(),
                    "python":sys.version,"platform":platform.platform(),"duration_seconds":round(time.perf_counter()-started,2),
                    "limitations":"Synthetic cases. Offline: lexical/route/tool contracts only. Term presence is not entailment. Missing scores are unmeasured, not passes.",
                    "overall":metrics(results),"categories":{k:metrics(v) for k,v in groups.items()},"cases":results}
                args.output.write_text(json.dumps(report,indent=2),encoding="utf-8")
        finally:
            if "file_ids_patch" in locals():file_ids_patch.stop()
            fixture.tearDown()
    return 0

if __name__=="__main__":raise SystemExit(main())
