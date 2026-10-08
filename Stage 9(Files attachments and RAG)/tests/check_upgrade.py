"""Upgrade regressions: no internet, model downloads, or production database."""
import asyncio
from contextlib import closing
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch, AsyncMock
from threading import Event
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import check_rag as fixtures
from RAG.evidence import assess, signals, strong_fast
from RAG.rag import Rag
from RAG.vector_cache import VectorCache
from RAG.request_state import RequestState, current
from RAG.operations import previous_operation, resolve
from RAG.grounding import review
from RAG.context_budget import evidence_messages, check

class EvidenceTests(unittest.TestCase):
    def test_reranking_rescues_low_raw_scores(self):
        row={"id":"a","file_id":"f","text":"Workers may work away from the office.","score":.03,
             "relevance":3,"dense_score":.24,"lexical_overlap":0}
        selected,decision=assess([row])
        self.assertTrue(decision["answerable"])
        self.assertEqual(Rag.diversify(selected),[row])
    def test_high_rank_is_not_answerability(self):
        row={"id":"a","file_id":"f","text":"Bananas ripen in a bowl.","score":.04,"dense_score":.9,
             **signals("What is the turbine inspection interval?","Bananas ripen in a bowl.")}
        self.assertEqual(assess([row])[0],[])
        row["relevance"]=0
        self.assertEqual(assess([row])[0],[])
    def test_exact_identifier_and_late_terms(self):
        text=" ".join(["filler"]*40)+" E_CONN-42 means link refused."
        self.assertTrue(signals("What does E_CONN-42 mean?",text)["identifier_match"])
    def test_fast_requires_retriever_agreement_and_no_tie(self):
        row={"id":"a","file_id":"f","text":"Turbine interval is 12 days.",**signals("Turbine interval?","Turbine interval is 12 days."),"dense_rank":1,"lexical_rank":1}
        self.assertTrue(strong_fast([row]))
        self.assertFalse(strong_fast([row,{**row,"id":"b","text":"Turbine interval is 24 days."}]))
    def test_cache_storage_bound_and_float32(self):
        cache=VectorCache(limit=1800)
        values=cache.decode([{"id":"a","vector":json.dumps([1,2,3]*30)}])
        self.assertEqual(values["a"].itemsize,4)
        for i in range(20):cache.put((1,str(i),"c","p",1),values)
        self.assertLessEqual(cache.bytes(),cache.limit)
        cache.invalidate(1)
        self.assertFalse(cache.entries)

class UpgradeTests(unittest.TestCase):
    setUp=fixtures.Checks.setUp
    tearDown=fixtures.Checks.tearDown
    upload=fixtures.Checks.upload
    send=fixtures.Checks.send
    rows=fixtures.Checks.rows

    def details(self):
        with closing(self.app.get_connection()) as db:
            row=db.execute("SELECT details FROM rag_runs ORDER BY message_id DESC LIMIT 1").fetchone()
        return json.loads(row[0]) if row else {}

    def test_fast_avoids_rewrite_and_reranking(self):
        file=self.upload()
        with patch.object(self.app.rag,"rerank",wraps=self.app.rag.rerank) as rank:
            events=self.send(use_files=True,file_ids=[file["id"]])
        self.assertEqual(events[-1]["status"],"completed")
        rank.assert_not_called()
        self.assertEqual(self.details()["mode"],"FAST")
        self.assertEqual(self.details()["model_calls"].get("generation"),1)
        self.assertIsNotNone(self.details()["time_to_first_token_ms"])

    def test_atomic_metadata_failure_and_restart(self):
        file=self.upload()
        with patch.object(self.app.documents,"persist_tools",side_effect=RuntimeError("injected evidence failure")):
            events=self.send("How many times does Falcon appear?",use_files=True,file_ids=[file["id"]])
        self.assertEqual(events[-1]["status"],"error")
        self.assertEqual(self.rows()[-1]["status"],"error")
        with closing(self.app.get_connection()) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM rag_runs").fetchone()[0],0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tool_runs").fetchone()[0],0)
        self.client.__exit__(None,None,None);self.client.__enter__()
        self.assertEqual(self.rows()[-1]["status"],"error")
        self.assertIn("Count:",self.rows()[-1]["content"])

    def test_quarter_followup_recalculates_without_model(self):
        file=self.upload("sales.csv",b"Quarter,Revenue\nQ3,12\nQ4,42","text/csv")
        self.send("What is the total Revenue for Q3?",use_files=True,file_ids=[file["id"]])
        before=len(self.calls)
        events=self.send("What about Q4?",use_files=True,file_ids=[file["id"]])
        self.assertEqual(events[-1]["status"],"completed",events)
        self.assertIn("42",self.rows()[-1]["content"])
        self.assertEqual(len(self.calls),before)
        self.assertEqual(self.details()["operation"]["tools"][0]["arguments"]["filters"][0]["value"],"Q4")

    def test_count_followup_switches_only_to_authorized_file(self):
        a=self.upload("A.txt",b"transformer transformer")
        b=self.upload("B.txt",b"transformer")
        self.send("How many times does transformer appear?",use_files=True,file_ids=[a["id"]])
        events=self.send("And in document B?",use_files=True,file_ids=[b["id"]])
        self.assertEqual(events[-1]["status"],"completed")
        self.assertIn("**1**",self.rows()[-1]["content"])
        self.assertEqual(self.details()["operation"]["tools"][0]["file_id"],b["id"])

    def test_followup_uses_selected_branch(self):
        a=self.upload("A.txt",b"falcon falcon transformer")
        self.send("How many times does falcon appear?",use_files=True,file_ids=[a["id"]])
        first_user=self.rows()[0]["id"];first_assistant=self.rows()[1]["id"]
        self.send("How many times does transformer appear?",action="edit",message_id=first_user,use_files=True,file_ids=[a["id"]])
        self.send("And in document A?",parent_id=first_assistant,use_files=True,file_ids=[a["id"]])
        self.assertIn("**2**",self.rows()[-1]["content"])
        self.assertEqual(self.details()["operation"]["route"]["term"],"falcon")

    def test_query_embedding_reuse_and_cache_scope(self):
        file=self.upload()
        async def run():
            token=current.set(RequestState())
            try:
                client=self.app.provider.client()
                for _ in range(2):await self.app.rag.retrieve(client,self.cid,[file["id"]],"project codename",mode="FAST")
                await client.close()
            finally:current.reset(token)
        before=self.embeds
        asyncio.run(run())
        self.assertEqual(self.embeds-before,1)
        self.assertGreater(self.app.rag.vector_cache.hits,0)
        other=self.client.post("/conversations").json()["id"]
        with self.assertRaises(ValueError):asyncio.run(self.app.rag.retrieve(self.app.provider.client(),other,[file["id"]],"project codename"))

    def test_stale_inflight_cache_load_cannot_publish(self):
        file=self.upload()
        with closing(self.app.get_connection()) as db:
            rows=[dict(r) for r in db.execute("SELECT * FROM chunks WHERE file_id=?",(file["id"],))]
        generation=self.app.documents.row(self.cid,file["id"])["index_generation"]
        entered=Event();resume=Event()
        original=self.app.rag.vector_cache.decode
        def delayed(rows):
            entered.set();resume.wait(5)
            return original(rows)
        with patch.object(self.app.rag.vector_cache,"decode",side_effect=delayed),ThreadPoolExecutor(1) as pool:
            future=pool.submit(self.app.rag.pack_vectors,self.cid,rows,{file["id"]:generation},rows[0]["config"])
            self.assertTrue(entered.wait(5))
            with closing(self.app.get_connection()) as db,db:
                db.execute("UPDATE chunks SET vector=? WHERE file_id=?",(json.dumps([0,1,0,0]),file["id"]))
            resume.set()
            with self.assertRaisesRegex(ValueError,"changed"):future.result()
        self.assertFalse(self.app.rag.vector_cache.entries)

    def test_generation_changes_transactionally_and_rollback_does_not(self):
        file=self.upload()
        before=self.app.documents.row(self.cid,file["id"])["index_generation"]
        with closing(self.app.get_connection()) as db:
            db.execute("BEGIN")
            db.execute("UPDATE chunks SET text=text||' changed' WHERE file_id=?",(file["id"],))
            self.assertGreater(db.execute("SELECT index_generation FROM files WHERE id=?",(file["id"],)).fetchone()[0],before)
            db.rollback()
        self.assertEqual(self.app.documents.row(self.cid,file["id"])["index_generation"],before)

    def test_review_modes_failure_and_no_silent_rewrite(self):
        file=self.upload()
        async def run():
            source=(await self.app.rag.retrieve(self.app.provider.client(),self.cid,[file["id"]],"project codename",mode="FAST"))[0][0]
            for mode,expected in (("off",False),("auto",False),("always",True)):
                result={"sources":[source],"debug":{"route":"DOCUMENT_QA","mode":"FAST"}}
                job={"conversation_id":self.cid,"queue":asyncio.Queue()}
                with patch.dict(os.environ,{"RAG_CLAIM_REVIEW":mode}),patch.object(self.app.provider,"structured",new_callable=AsyncMock,return_value={"unsupported":False,"findings":[]}) as verify:
                    notice=await review(self.app.provider,self.app.documents,job,"Blue Falcon ["+source["id"]+"]",result)
                    self.assertEqual(verify.await_count,int(expected));self.assertEqual(notice,"")
            for mode in ("auto","always"):
                result={"sources":[source],"debug":{"route":"DOCUMENT_QA","mode":"DEEP"}}
                with patch.dict(os.environ,{"RAG_CLAIM_REVIEW":mode}),patch.object(self.app.provider,"structured",new_callable=AsyncMock,side_effect=TimeoutError):
                    notice=await review(self.app.provider,self.app.documents,job,"Blue Falcon",result)
                    self.assertEqual(notice,"")
                    self.assertEqual(result["debug"]["claim_review"]["status"],"unavailable")
        asyncio.run(run())

    def test_review_warning_is_same_in_stream_and_database(self):
        file=self.upload()
        original=self.app.provider.structured
        async def response(messages,schema,**kw):
            if "unsupported" in schema.get("properties",{}):
                self.assertTrue(self.app.provider.text_slots.acquire(blocking=False))
                self.app.provider.text_slots.release()
                return {"unsupported":True,"findings":["Claim not supported."]}
            return await original(messages,schema,**kw)
        with patch.dict(os.environ,{"RAG_CLAIM_REVIEW":"always"}),patch.object(self.app.provider,"structured",side_effect=response):
            events=self.send(use_files=True,file_ids=[file["id"]])
        text="".join(e["content"] for e in events if e["type"]=="delta")
        self.assertIn("Advisory model review",text)
        self.assertEqual(text,self.rows()[-1]["content"])
        self.assertTrue(any("Checking cited" in e.get("message","") for e in events))
        self.assertEqual(events[-1]["status"],"completed")

    def test_summary_digest_prompt_invalidation_and_final_reduction(self):
        file=self.upload("summary.md",b"# One\n\nA complete paragraph about magnetic bearings.","text/markdown")
        state={"digest":"model-a","calls":0}
        class Client:
            async def list(inner):return {"models":[{"model":"llama3.2:3b","digest":state["digest"]}]}
            async def chat(inner,**kwargs):
                state["calls"]+=1
                return {"message":{"content":"Summary of magnetic bearings." if kwargs["options"]["num_predict"]<900 else "Useful complete summary. "*45}}
            async def close(inner):pass
        async def run():
            with patch.object(self.app.provider,"client",return_value=Client()):
                first=await self.app.document_agent.summarizer.summarize(self.cid,[file],lambda _:None,final_budget=350)
                self.assertGreater(first[2]["final_reductions"],0)
                before=state["calls"]
                await self.app.document_agent.summarizer.summarize(self.cid,[file],lambda _:None,final_budget=350)
                self.assertEqual(state["calls"],before)
                state["digest"]="model-b"
                await self.app.document_agent.summarizer.summarize(self.cid,[file],lambda _:None,final_budget=350)
                self.assertGreater(state["calls"],before)
                before=state["calls"]
                with patch("RAG.summarizer.PROMPT_VERSION","test-new-prompt"):
                    await self.app.document_agent.summarizer.summarize(self.cid,[file],lambda _:None,final_budget=350)
                self.assertGreater(state["calls"],before)
        asyncio.run(run())

    def test_section_and_comparison_context_resolution(self):
        from RAG.document_agent import Route
        from RAG.operations import record
        a=self.upload("sections.md",b"# Section 3\n\nCapacity is limited by cooling.","text/markdown")
        self.send(use_files=True,file_ids=[a["id"]])
        assistant=self.rows()[-1]["id"]
        def seed(route,files):
            with closing(self.app.get_connection()) as db,db:
                db.execute("UPDATE rag_runs SET details=? WHERE message_id=?",(json.dumps({"operation":record(route,files,"previous topic")}),assistant))
        job={"conversation_id":self.cid,"user_message_id":assistant}
        seed(Route(route="SUMMARIZE_SECTION",section="3"),[a])
        result=resolve(self.app.documents,job,"Explain its limitations.",[a])
        self.assertEqual(result["route"]["route"],"DOCUMENT_QA")
        self.assertEqual(result["route"]["section"],"3")
        b=self.upload("second.txt",b"Another document.")
        seed(Route(route="COMPARE_DOCUMENTS"),[a,b])
        result=resolve(self.app.documents,job,"What about termination clauses?",[a,b])
        self.assertEqual(result["selected_ids"],[a["id"],b["id"]])
        self.assertEqual(result["route"]["route"],"COMPARE_DOCUMENTS")

    def test_quotation_validation_and_review_cancellation(self):
        from RAG.grounding import deterministic
        file=self.upload()
        async def run():
            source=(await self.app.rag.retrieve(self.app.provider.client(),self.cid,[file["id"]],"project codename",mode="FAST"))[0][0]
            result={"sources":[source],"debug":{"route":"DOCUMENT_QA","mode":"DEEP"}}
            facts=deterministic(self.app.documents,self.cid,'"Red Heron" ['+source["id"]+']',result)
            self.assertEqual(facts[0]["kind"],"quotation")
            entered=asyncio.Event()
            async def waiting(*args,**kwargs):entered.set();await asyncio.sleep(60)
            with patch.dict(os.environ,{"RAG_CLAIM_REVIEW":"auto"}),patch.object(self.app.provider,"structured",side_effect=waiting):
                task=asyncio.create_task(review(self.app.provider,self.app.documents,{"conversation_id":self.cid,"queue":asyncio.Queue()},"Blue Falcon",result))
                await asyncio.wait_for(entered.wait(),1);task.cancel()
                with self.assertRaises(asyncio.CancelledError):await task
        asyncio.run(run())

    def test_reindex_cache_invalidates_and_deleted_file_evicts(self):
        file=self.upload()
        self.send(use_files=True,file_ids=[file["id"]])
        cache=self.app.rag.vector_cache
        self.assertTrue(cache.entries)
        previous_keys=set(cache.entries)
        with closing(self.app.get_connection()) as db,db:
            db.execute("UPDATE chunks SET vector=vector WHERE file_id=?",(file["id"],))
        self.send(use_files=True,file_ids=[file["id"]])
        self.assertFalse(previous_keys&set(cache.entries))
        self.client.delete(f"/conversations/{self.cid}/files/{file['id']}")
        self.assertFalse(cache.entries)

    def test_package_loaded_once_and_evaluation_split(self):
        self.assertNotIn("rag",sys.modules)
        self.assertNotIn("documents",sys.modules)
        corpus=json.loads((Path(__file__).parent/"evaluation_cases.json").read_text(encoding="utf-8"))
        dev=[f for f in corpus["families"] if f["split"]=="development"]
        held=[f for f in corpus["families"] if f["split"]=="held_out"]
        self.assertEqual(sum(len(f["cases"]) for f in dev),40)
        self.assertEqual(sum(len(f["cases"]) for f in held),40)
        self.assertFalse({f["family"] for f in dev}&{f["family"] for f in held})
        self.assertFalse({d["text"] for f in dev for d in f["files"]}&{d["text"] for f in held for d in f["files"]})

    def test_deep_round_limit_and_selected_scope(self):
        a=self.upload("security.txt",b"Security requirements mandate TLS encryption.")
        b=self.upload("unrelated.txt",b"Flowers grow in spring.")
        async def structured(messages,schema,**kwargs):
            if "query" in schema.get("properties",{}):return {"query":"missing security requirements"}
            raise ValueError("Use deterministic fallback")
        with patch.object(self.app.provider,"structured",side_effect=structured),patch.object(self.app.rag,"retrieve",wraps=self.app.rag.retrieve) as search:
            events=self.send("Compare security requirements.",use_files=True,file_ids=[a["id"],b["id"]])
        self.assertEqual(events[-1]["status"],"completed")
        self.assertEqual(search.call_count,2)
        self.assertEqual(self.details()["rounds"],2)
        for call in search.call_args_list:self.assertEqual(set(call.args[2]),{a["id"],b["id"]})

    def test_cache_generation_changes_while_query_embedding_waits(self):
        file=self.upload()
        async def run():
            client=self.app.provider.client()
            original=client.embed
            async def changed(**kwargs):
                result=await original(**kwargs)
                with closing(self.app.get_connection()) as db,db:
                    db.execute("UPDATE chunks SET vector=vector WHERE file_id=?",(file["id"],))
                return result
            with patch.object(client,"embed",side_effect=changed):
                with self.assertRaisesRegex(ValueError,"changed"):
                    await self.app.rag.retrieve(client,self.cid,[file["id"]],"project codename",mode="FAST")
            await client.close()
        asyncio.run(run())
        self.assertFalse(self.app.rag.vector_cache.entries)

    def test_stop_during_api_claim_review(self):
        from uuid import uuid4
        file=self.upload()
        async def run():
            entered=asyncio.Event()
            async def waiting(*args,**kwargs):entered.set();await asyncio.sleep(60)
            with patch.dict(os.environ,{"RAG_CLAIM_REVIEW":"always"}),patch.object(self.app.provider,"structured",side_effect=waiting):
                gid=uuid4()
                response=await self.app.chat(self.app.ChatRequest(conversation_id=self.cid,generation_id=gid,
                    message="What is the project codename?",use_files=True,file_ids=[file["id"]]))
                await asyncio.wait_for(entered.wait(),3)
                self.assertEqual((await self.app.stop_generation(gid))["status"],"stopped")
                await response.body_iterator.aclose()
            self.assertTrue(self.app.provider.text_slots.acquire(blocking=False))
            self.app.provider.text_slots.release()
        asyncio.run(run())
        self.assertEqual(self.rows()[-1]["status"],"stopped")

if __name__=="__main__":unittest.main()
