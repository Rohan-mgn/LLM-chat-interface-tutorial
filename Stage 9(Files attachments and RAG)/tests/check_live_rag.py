"""Live local-model RAG evaluation. Uses a disposable DB, never your saved chats.

Output separates objective retrieval metrics from answer heuristics; review the
saved answers with the rubric in README.md for groundedness/relevance.
"""
import asyncio
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import re
import shutil
import sys
import time
from unittest.mock import patch
from uuid import uuid4
import ollama
from fastapi.testclient import TestClient

STAGE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(STAGE))
from RAG.evidence import choose_mode

def main():
    spec=importlib.util.spec_from_file_location("stage9_live",STAGE/"main.py")
    app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)
    # Report missing local prerequisites separately from pipeline regressions.
    model_client=ollama.Client(timeout=5)
    try:
        available={m["model"] for m in model_client.list()["models"]}
    except (ConnectionError, OSError) as error:
        print("LIVE TEST NOT RUN: Ollama is unavailable. Start Ollama and rerun this test.",flush=True)
        raise SystemExit(2) from error
    finally:
        if hasattr(model_client,"close"):model_client.close()
    needed=(app.provider.chat_model,app.provider.embed_model)
    missing=[name for name in needed if name not in available and name+":latest" not in available]
    if missing:
        print("LIVE TEST NOT RUN: required local models are missing: "+", ".join(missing),flush=True)
        raise SystemExit(2)
    root=STAGE / "tests" / ".artifacts"/("stage9-live-"+uuid4().hex)
    app.DATA_DIR=root;app.DATABASE=root/"chat.db"
    results=[]
    try:
        with patch.object(app.ollama,"Client",side_effect=RuntimeError("Skip optional titles")), TestClient(app.app) as client:
            cid=client.post("/conversations").json()["id"]
            def upload(name,text,mime):
                response=client.post(f"/conversations/{cid}/files",files={"upload":(name,text.encode(),mime)})
                assert response.status_code==201,response.text
                fid=response.json()["file"]["id"]
                start=time.perf_counter()
                while time.perf_counter()-start<180:
                    row=app.documents.row(cid,fid)
                    if row["state"]=="ready":return fid
                    assert row["state"]!="failed",row["error"]
                    time.sleep(.2)
                raise AssertionError("Indexing timed out")
            project=upload("project.md","# Company project\n\nThe internal company project codename is Blue Falcon. Its private test code is 8472.","text/markdown")
            sales=upload("sales.csv","quarter,revenue_usd\nQ3,12000\nQ4,42000\n","text/csv")
            irrelevant=upload("garden.txt","This gardening note describes watering roses twice a week and adding compost to soil.","text/plain")
            fixtures=[
                ("What is the internal company project codename?",{project},"Blue Falcon"),
                ("What is the private project test code?",{project},"8472"),
                ("What was sales revenue in Q4?",{sales},"42000"),
            ]
            async def retrieval_eval():
                model=app.provider.client(timeout=120)
                try:
                    for query,expected,answer in fixtures:
                        started=time.perf_counter()
                        try:
                            hits,_=await app.rag.retrieve(model,cid,[project,sales,irrelevant],query,mode=choose_mode(query))
                        except ValueError:
                            current=await app.rag.descriptor(model)
                            with closing(app.get_connection()) as db:
                                indexed=[dict(row) for row in db.execute("SELECT original_filename,config,state FROM files")]
                            print(json.dumps({"current_embedding_config":current,"indexed_files":indexed}),flush=True)
                            raise
                        matched=[h["file_id"] in expected for h in hits]
                        found={h["file_id"] for h in hits}&expected
                        results.append({"query":query,"expected_files":sorted(expected),
                            "hits":[{"id":h["id"],"file_id":h["file_id"],"score":round(h["score"],4),"backend":h.get("retrieval_backend"),"warning":h.get("retrieval_warning")} for h in hits],
                            "hit_at_k":int(bool(found)),"precision_at_returned_k":sum(matched)/len(hits) if hits else 0,
                            "file_recall_at_k":len(found)/len(expected),
                            "reciprocal_rank":next((1/(i+1) for i,v in enumerate(matched) if v),0),
                            "retrieval_ms":round(1000*(time.perf_counter()-started),1)})
                finally:await model.close()
            asyncio.run(retrieval_eval())
            def chat(text,files,conversation=cid):
                started=time.perf_counter()
                response=client.post("/chat",json={"conversation_id":conversation,"generation_id":str(uuid4()),
                    "message":text,"use_files":bool(files),"file_ids":files})
                assert response.status_code==200,response.text
                events=[json.loads(p[6:]) for p in response.text.strip().split("\n\n")]
                assert events[-1]["status"]=="completed",events
                text="".join(e.get("content","") for e in events if e["type"]=="delta")
                rows=client.get(f"/conversations/{conversation}/tree").json()["messages"]
                return text,rows[-1],round(1000*(time.perf_counter()-started),1)
            answer,row,latency=chat(fixtures[0][0],[project])
            with closing(app.get_connection()) as db:
                answer_diagnostics=json.loads(db.execute("SELECT details FROM rag_runs WHERE message_id=?",(row["id"],)).fetchone()[0])
            sources=row["sources"]
            valid_ids={s["id"] for s in sources}
            references=re.findall(r"\[(SOURCE_[^\]]+)\]",answer)
            summary={"retrieval":results,
                "mean_hit_at_k":sum(x["hit_at_k"] for x in results)/len(results),
                "mrr":sum(x["reciprocal_rank"] for x in results)/len(results),
                "answer":answer,"answer_latency_ms":latency,"answer_diagnostics":answer_diagnostics,
                "expected_fact_present":"blue falcon" in answer.casefold(),
                "citation_id_precision":sum(s in valid_ids for s in references)/len(references) if references else 0,
                "has_citation":bool(sources),
                "manual_review_required":["factual correctness","claim-level groundedness","answer relevance","citation entailment","citation coverage"]}
            print(json.dumps(summary,ensure_ascii=True,indent=2),flush=True)
            assert summary["mean_hit_at_k"]==1,"Retrieval missed an expected source"
            assert summary["expected_fact_present"] and summary["has_citation"],"Live answer missing fact/citation"
            other=client.post("/conversations").json()["id"]
            isolated,_,_=chat("What is the private project test code?",[],other)
            assert "8472" not in isolated,"Private fact leaked across conversations"
            # The follow-up passes the actual selected conversation through rewriting.
            follow,_,_=chat("What is its private test code?",[project])
            print(json.dumps({"followup_answer":follow,"isolated_answer":isolated}),flush=True)
            with closing(app.get_connection()) as db:
                print("Follow-up diagnostics:",db.execute("SELECT details FROM rag_runs ORDER BY message_id DESC LIMIT 1").fetchone()[0],flush=True)
            assert "8472" in follow,"Follow-up answer omitted the expected fact"
            exact,tool_row,_=chat("Count word Falcon",[project])
            assert "**1**" in exact and tool_row["tool_evidence"],"Exact count/provenance failed"
            with closing(app.get_connection()) as db:
                route=json.loads(db.execute("SELECT details FROM rag_runs WHERE message_id=?",(tool_row["id"],)).fetchone()[0])["route"]
            assert route=="COUNT"
            total,table_row,_=chat("What is total revenue_usd in sales.csv?",[sales])
            assert "54000" in total and table_row["tool_evidence"],"Table operation failed"
            print(json.dumps({"route_accuracy":1.0,"exact_count":exact,"table_calculation":total}),flush=True)
            before=client.get(f"/conversations/{cid}/tree").json()
        # A fresh application lifespan reopens the same DB without re-indexing.
        with TestClient(app.app) as client:
            assert client.get(f"/conversations/{cid}/tree").json()==before
            assert app.documents.row(cid,project)["state"]=="ready"
        print("PASS: real EmbeddingGemma retrieval, cited llama3.2 reply, follow-up, isolation, and restart persistence.",flush=True)
    finally:
        assert root.resolve().parent==(STAGE / "tests" / ".artifacts").resolve() and root.name.startswith("stage9-live-")
        if root.exists():shutil.rmtree(root)

if __name__=="__main__":main()
