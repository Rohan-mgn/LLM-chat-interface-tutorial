"""Opt-in live ordinal follow-up probe with a controlled prior answer.

Uses an isolated PRE_ACTIVATION database and installed models. --paired compares
retrieval with direct reading on the SAME conversation and source identities.
It is not a storage-backend comparison or a full multi-turn quality score.
"""
import argparse
import asyncio
from contextlib import closing, ExitStack
import hashlib
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import check_rag as fixtures
from RAG.model_provider import CHAT_MODEL, EMBED_MODEL, ProviderClient
from RAG.context_budget import evidence_messages

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live",action="store_true",required=True)
    parser.add_argument("--paired",action="store_true")
    parser.add_argument("--output",default="tests/results/answer-reference-live.json")
    args=parser.parse_args()
    import ollama
    models=ollama.Client(timeout=10).list()["models"]
    if not {CHAT_MODEL,EMBED_MODEL}.issubset({m["model"] for m in models}):
        raise SystemExit("Configured local models are unavailable; no downloads will occur.")
    fixture=fixtures.Checks(methodName="test_no_evidence_does_not_hallucinate_a_model_answer")
    fixture.setUp()
    document=("A bounded queue stores pending work up to a fixed capacity. "
              "When the queue is full, backpressure makes producers wait instead of creating more queued work. "
              "Consumers remove queued work when they have capacity. "
              "Backpressure limits pending work; it does not make consumers process faster.")
    question="Explain the second point again with a simple everyday analogy."
    try:
        file=fixture.upload("queues.txt",document.encode(),"text/plain")
        fixture.send("Summarize the document in two points.",use_files=True,file_ids=[file["id"]])
        prior=fixture.rows()[-1];sid=prior["sources"][0]["id"]
        controlled=("1. A bounded queue holds pending work up to a fixed capacity. ["+sid+"]\n"
                    "2. Backpressure makes producers wait when the queue is full; it does not make consumers faster. ["+sid+"]")
        with closing(fixture.app.get_connection()) as db,db:
            db.execute("UPDATE messages SET content=? WHERE id=?",(controlled,prior["id"]))
        fixture.mock.stop()
        index_started=time.perf_counter()
        asyncio.run(fixture.app.rag.index(fixture.cid,file["id"]))
        if fixture.app.documents.row(fixture.cid,file["id"])["state"]!="ready":
            raise RuntimeError("Live fixture indexing did not complete.")
        report={"kind":"controlled-prior-answer live development probe; not full assistant acceptance",
            "models":[{"model":m["model"],"digest":m["digest"]} for m in models],
            "source_document":document,"document_sha256":hashlib.sha256(document.encode()).hexdigest(),
            "prior_answer":controlled,"question":question,
            "indexing_seconds":round(time.perf_counter()-index_started,3),"rows":[],
            "limitations":"Single development observation per strategy, controlled prior answer, retrieval first/direct second. Executed after the regression suite. Strategy order, cache state and sampling can affect timing; not a percentage-speedup claim. Human review needed for analogy correctness."}
        previous=None
        for strategy in (("retrieval","direct") if args.paired else ("direct",)):
            prompts=[]
            original_chat=ProviderClient.chat
            async def chat(client,**kwargs):
                if kwargs.get("stream"):
                    payload={k:kwargs.get(k) for k in ("model","messages","options")}
                    prompts.append(hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest())
                return await original_chat(client,**kwargs)
            def retrieve_packet(*arguments,**keywords):
                return None if keywords.get("require_all") else evidence_messages(*arguments,**keywords)
            started=time.perf_counter()
            with ExitStack() as patches:
                patches.enter_context(patch.object(ProviderClient,"chat",chat))
                if strategy=="retrieval":
                    patches.enter_context(patch("RAG.document_agent.evidence_messages",side_effect=retrieve_packet))
                extra={"action":"regenerate","message_id":previous} if previous is not None else {}
                events=fixture.send(question,use_files=True,file_ids=[file["id"]],**extra)
            seconds=time.perf_counter()-started
            answer_row=fixture.rows()[-1];previous=answer_row["id"];answer=answer_row["content"]
            with closing(fixture.app.get_connection()) as db:
                row=db.execute("SELECT details FROM rag_runs WHERE message_id=?",(previous,)).fetchone()
            details=json.loads(row[0]) if row else {}
            result={"strategy":strategy,"answer":answer,"events":events,"seconds":round(seconds,3),
                "prompt_fingerprints":prompts,"details":details,
                "deterministic_checks":{"completed":events[-1].get("status")=="completed",
                    "resolved_correct_prior_message":details.get("answer_reference",{}).get("message_id")==prior["id"],
                    "resolved_second_point":details.get("answer_reference",{}).get("ordinal")==2,
                    "reused_original_section_number":bool(details.get("operation",{}).get("route",{}).get("section")),
                    "has_valid_citation":any("["+s["id"]+"]" in answer for s in answer_row.get("sources",[]))}}
            report["rows"].append(result)
            report["complete"]=len(report["rows"])==(2 if args.paired else 1)
            if args.paired and report["complete"]:
                report["identical_generation_prompts"]=bool(report["rows"][0]["prompt_fingerprints"]) and report["rows"][0]["prompt_fingerprints"]==report["rows"][1]["prompt_fingerprints"]
            Path(args.output).write_text(json.dumps(report,indent=2),encoding="utf-8")
            print(json.dumps({"strategy":strategy,"seconds":result["seconds"],"checks":result["deterministic_checks"]}),flush=True)
    finally:fixture.tearDown()

if __name__=="__main__":main()
