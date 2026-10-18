"""Opt-in development evaluation of real local-model table interpretation.

Isolated ingestion fixtures; real production chat path after ingestion. No model
downloads or production database access. Expected results are fixed before runs.
"""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sys
import time
from copy import deepcopy
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import check_rag as fixtures
from RAG.model_provider import CHAT_MODEL

CASES=[
    {"id":"period-percentage","document":"Period,Amount\nQ1,100\nQ2,125\n",
     "question":"How did Amount change from Q1 to Q2 as a percentage?",
     "expected":{"metric":"percent_change","value":"25.000000"}},
    {"id":"department-ranking","document":"Department,Spend\nEngineering,80\nSupport,50\nEngineering,20\n",
     "question":"Which department spent more money, and why?",
     "expected":{"rank_group":{"Department":"Engineering"},"value":"100"}},
    {"id":"zero-baseline","document":"Period,Amount\nQ1,0\nQ2,40\n",
     "question":"What is the percentage increase in Amount from Q1 to Q2?",
     "expected":{"metric":"percent_change","value":None,"status":"undefined_zero_baseline"}},
    {"id":"filtered-comparison","document":"Period,Region,Amount\nQ1,East,50\nQ2,East,75\nQ1,West,1000\nQ2,West,1\n",
     "question":"For East only, what is the percentage change in Amount from Q1 to Q2?",
     "expected":{"metric":"percent_change","value":"50.000000"}},
    {"id":"missing-period","document":"Period,Amount\nQ1,100\nQ2,125\n",
     "question":"What is the percentage change in Amount from Q1 to Q4?",
     "expected":{"comparison_status":"missing_group"}},
]


def grade(evidence, expected):
    if not evidence:return False
    result=evidence[0]["exact_result"]
    if "rank_groups" in expected:
        entries=result.get("ranking",{}).get("entries",[])
        return [e["group"] for e in entries]==expected["rank_groups"] and all(e["rank"]==1 and e["value"]==expected["value"] for e in entries)
    if "rank_group" in expected:
        entries=result.get("ranking",{}).get("entries",[])
        return bool(entries and entries[0]["group"]==expected["rank_group"] and entries[0]["value"]==expected["value"])
    comparisons=result.get("comparisons",[])
    if "comparison_status" in expected:
        return any(c["status"]==expected["comparison_status"] for c in comparisons)
    return any(c.get("metrics",{}).get(expected["metric"],{}).get("status")==expected.get("status","ok") and
               c["metrics"][expected["metric"]]["value"]==expected["value"] for c in comparisons)


def run(case, candidate=False):
    fixture=fixtures.Checks(methodName="test_no_evidence_does_not_hallucinate_a_model_answer")
    fixture.setUp()
    try:
        file=fixture.upload("comparison.csv",case["document"].encode(),"text/csv")
        fixture.mock.stop()
        interpretations=[]
        original=fixture.app.provider.structured
        async def interpreted(messages,schema,**kwargs):
            schema=deepcopy(schema);messages=deepcopy(messages)
            if candidate and "comparisons" in schema.get("properties",{}):
                schema["required"]+= [name for name in ("comparisons","ranking") if name not in schema["required"]]
                messages[0]["content"]+=(" Always return comparisons and ranking. Use comparisons for period changes and ranking for which group spent more. "
                    "For comparisons, group_by must be exactly the column keys in baseline and current. A filter column is not a group column unless the requested comparison also groups by it. "
                    "Group spending is the SUM of its transactions, unless the user explicitly asks for another aggregate; MAX only answers largest single transaction. "
                    "For example: group_by=['Period'], aggregates=[{'op':'sum','column':'Amount'}], comparisons=[{'aggregate':0,'baseline':{'Period':'Q1'},'current':{'Period':'Q2'},'metrics':['percent_change']}], ranking=null. "
                    "For highest total by department: group_by=['Department'], aggregates=[{'op':'sum','column':'Spend'}], comparisons=[], ranking={'aggregate':0,'direction':'descending','limit':1}.")
            start=time.perf_counter()
            trace={"schema_sha256":hashlib.sha256(json.dumps(schema,sort_keys=True).encode()).hexdigest(),
                   "prompt_sha256":hashlib.sha256(json.dumps(messages,sort_keys=True).encode()).hexdigest()}
            try:
                response=await original(messages,schema,**kwargs)
                trace["result"]=response
                return response
            except Exception as error:
                trace["error"]=type(error).__name__+": "+str(error)
                raise
            finally:
                trace["seconds"]=round(time.perf_counter()-start,3);interpretations.append(trace)
        started=time.perf_counter()
        with patch.object(fixture.app.provider,"structured",interpreted):
            events=fixture.send(case["question"],use_files=True,file_ids=[file["id"]])
        seconds=time.perf_counter()-started
        reply=fixture.rows()[-1]
        evidence=[fixture.client.get(f"/conversations/{fixture.cid}/tool-evidence/{tool['id']}").json()
                  for tool in reply.get("tool_evidence",[])]
        with closing(fixture.app.get_connection()) as db:
            row=db.execute("SELECT details FROM rag_runs ORDER BY message_id DESC LIMIT 1").fetchone()
        return {"case":case,"seconds":round(seconds,3),"answer":reply["content"],
                "events":events,"evidence":evidence,"interpretations":interpretations,"details":json.loads(row[0]) if row else {},
                "passed":events[-1].get("status")=="completed" and grade(evidence,case["expected"])}
    finally:fixture.tearDown()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live",action="store_true",required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--candidate",action="store_true",help="Development-only required fields and aggregation examples; production stays unchanged.")
    parser.add_argument("--cases",type=Path,help="Use a frozen alternate fixture instead of development cases.")
    args=parser.parse_args()
    import ollama
    models=ollama.Client(timeout=10).list()["models"]
    if not any(m["model"]==CHAT_MODEL for m in models):raise SystemExit("Configured chat model unavailable; no downloads.")
    cases=json.loads(args.cases.read_text(encoding="utf-8"))["cases"] if args.cases else CASES
    report={"kind":"table interpretation probe", "fixture":str(args.cases) if args.cases else "built-in development",
        "fixture_sha256":hashlib.sha256(json.dumps(cases,sort_keys=True).encode()).hexdigest(),
        "models":[{"model":m["model"],"digest":m["digest"]} for m in models],"rows":[],"complete":False,"candidate":args.candidate,
        "limitations":"Single observation per case; may overlap regression work. Grader checks deterministic results, not semantic correctness of every argument, explanations or general assistant quality. Ingestion is mocked and untimed. Not held-out acceptance or controlled latency benchmarking."}
    for case in cases:
        try:row=run(case,args.candidate)
        except Exception as error:row={"case":case,"passed":False,"error":str(error)}
        report["rows"].append(row)
        args.output.write_text(json.dumps(report,indent=2),encoding="utf-8")
        print(case["id"],row["passed"],row.get("seconds"),flush=True)
    report.update(complete=True,passed=sum(r["passed"] for r in report["rows"]),total=len(cases))
    args.output.write_text(json.dumps(report,indent=2),encoding="utf-8")


if __name__=="__main__":main()
