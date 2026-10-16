"""Opt-in native-vs-structured first-decision qualification on frozen dev cases.

No document tools are executed and no production routing setting is changed.
Independent expected actions/scope are used; the model does not grade itself.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from RAG.planner import Planner,Task,Tool,InvalidDecision
from RAG.model_provider import ModelProvider
from RAG.request_state import RequestState,current,model_digest

def catalog(attachments):
    aliases=[a["alias"] for a in attachments if a["state"] not in ("unreadable","metadata_only")]
    if not aliases:return []
    single={"type":"string","enum":aliases}
    plural={"type":"array","items":single,"minItems":1,"maxItems":len(aliases),"uniqueItems":True}
    definitions=[
        ("read_document","Read full small text, a page/section/slide/lines, or an outline.",
         {"file":single,"view":{"type":"string","enum":["full","page","section","slide","lines","outline"]},"locator":{"type":"string","maxLength":200}}),
        ("search_documents","Find targeted factual evidence in the specified documents.",
         {"files":plural,"query":{"type":"string","minLength":1,"maxLength":400}}),
        ("process_document","Process every required section of large documents for the original comprehensive objective.",
         {"files":plural})]
    if any(a["format"] in ("xlsx","csv") for a in attachments):
        definitions.append(("inspect_table","Inspect actual sheets, columns and periods before choosing an exact numerical operation.",{"file":single}))
    if any(a["format"] in ("py","js","ts") for a in attachments):
        definitions.append(("inspect_code","Inspect source structure and line references without executing code.",{"file":single}))
    result=[]
    for name,description,properties in definitions:
        spec={"type":"object","properties":properties,"required":list(properties),"additionalProperties":False}
        def validate(args,properties=properties):
            if set(args)!=set(properties):raise ValueError("Unexpected arguments.")
            for key,value in args.items():
                rule=properties[key]
                if rule["type"]=="string":
                    if not isinstance(value,str) or len(value)>rule.get("maxLength",400):raise ValueError("Invalid string.")
                    if "enum" in rule and value not in rule["enum"]:raise ValueError("Out of scope.")
                    if rule.get("minLength",0)>len(value):raise ValueError("Empty argument.")
                else:
                    if not isinstance(value,list) or not value or len(value)!=len(set(value)) or any(v not in aliases for v in value):
                        raise ValueError("Out of scope.")
            return args
        async def never_execute(args):raise AssertionError("Qualification must not execute tools.")
        result.append(Tool(name,description,spec,validate,never_execute))
    return result

async def qualify(args):
    fixture=Path(__file__).with_name("planner_development.json")
    frozen=json.loads(fixture.read_text(encoding="utf-8"))
    cases=frozen["cases"][:args.limit]
    provider=ModelProvider()
    digest=await model_digest(provider,provider.chat_model)
    if not digest:raise SystemExit("The configured local chat model is unavailable. No model will be downloaded.")
    report={"protocol":"attachment-plan-v1","kind":"first-decision development qualification; not full assistant acceptance",
        "fixture_sha256":hashlib.sha256(fixture.read_bytes()).hexdigest(),
        "model":provider.chat_model,"model_digest":digest,"rows":[],"complete":False,"qualified":False,
        "limitations":"One decision per case and method; does not measure multi-step tool execution, final answer faithfulness, or held-out reliability."}
    for case in cases:
        for method in ("structured","native"):
            task=Task(case["question"],case["attachments"],history=case.get("history",[]))
            task.steps=1
            planner=Planner(provider,catalog(case["attachments"]),method=method)
            state=RequestState();token=current.set(state)
            started=time.perf_counter();decision=None;error=None
            try:
                decision=await planner.decide(task)
            except Exception as failure:
                error=type(failure).__name__+": "+str(failure)[:180]
            finally:current.reset(token)
            action=decision["name"] if decision and decision["action"]=="tool" else decision.get("action") if decision else None
            arguments=decision.get("arguments",{}) if decision else {}
            scope=arguments.get("files",[arguments["file"]] if "file" in arguments else [])
            correct=action==case["expected"] and sorted(scope)==sorted(case["files"])
            row={"id":case["id"],"method":method,"valid":decision is not None,"correct":correct,
                 "decision":decision,"error":error,"seconds":round(time.perf_counter()-started,3),"metrics":state.report()}
            report["rows"].append(row)
            Path(args.output).write_text(json.dumps(report,indent=2),encoding="utf-8")
            print(case["id"],method,"valid",row["valid"],"correct",correct,"seconds",row["seconds"],flush=True)
    report["complete"]=len(cases)==len(frozen["cases"])
    report["summary"]={}
    for method in ("structured","native"):
        rows=[r for r in report["rows"] if r["method"]==method]
        summary={"valid_rate":sum(r["valid"] for r in rows)/len(rows),
                 "selection_scope_accuracy":sum(r["correct"] for r in rows)/len(rows),
                 "median_seconds":statistics.median(r["seconds"] for r in rows)}
        summary["meets_first_decision_thresholds"]=report["complete"] and summary["valid_rate"]>=frozen["minimum_valid_decisions"] and summary["selection_scope_accuracy"]>=frozen["minimum_correct_selection_and_scope"]
        report["summary"][method]=summary
    report["qualified"]=any(s["meets_first_decision_thresholds"] for s in report["summary"].values())
    Path(args.output).write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report["summary"]),flush=True)

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live",action="store_true",required=True)
    parser.add_argument("--limit",type=int,default=20)
    parser.add_argument("--output",default="tests/results/planner-development.json")
    args=parser.parse_args()
    if not 1<=args.limit<=20:parser.error("--limit must be 1..20")
    asyncio.run(qualify(args))
