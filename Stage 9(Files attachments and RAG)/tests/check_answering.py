"""Direct-context, task preservation and honest structural coverage regressions."""
import asyncio
from contextlib import closing
import json
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch, AsyncMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import check_rag as fixtures
from RAG.answering import (canonical_sources, complete_context, coverage_manifest,
    answer_coverage, bundle_metadata, task_state)
from RAG.context_budget import check

class AnsweringTests(unittest.TestCase):
    setUp=fixtures.Checks.setUp
    tearDown=fixtures.Checks.tearDown
    upload=fixtures.Checks.upload
    send=fixtures.Checks.send
    rows=fixtures.Checks.rows

    def details(self):
        with closing(self.app.get_connection()) as db:
            return json.loads(db.execute("SELECT details FROM rag_runs ORDER BY message_id DESC LIMIT 1").fetchone()[0])

    def test_small_complete_document_skips_summary_and_retrieval(self):
        file=self.upload("guide.md",b"# Start\n\nA pump moves water.\n\n# Finish\n\nThe valve stops the flow.","text/markdown")
        with patch.object(self.app.rag,"retrieve",AsyncMock(side_effect=AssertionError("No retrieval"))),patch.object(self.app.document_agent.summarizer,"summarize",AsyncMock(side_effect=AssertionError("No summary pass"))):
            events=self.send("Summarize the entire guide for an eight-year-old in two sections.",use_files=True,file_ids=[file["id"]])
        self.assertEqual(events[-1]["status"],"completed",events)
        details=self.details()
        self.assertEqual(details["acquisition"],"direct_complete")
        self.assertIn("eight-year-old",details["task_state"]["objective"])
        self.assertEqual(len([c for c in self.calls if c.get("stream")]),1)
        self.assertFalse([c for c in self.calls if not c.get("stream")])
        self.assertIn("valve",str(self.calls[-1]))
        self.assertIn("pump",str(self.calls[-1]))
        self.assertEqual(details["answer_coverage"]["required"],2)

    def test_parsed_document_works_without_semantic_index(self):
        file=self.upload()
        with closing(self.app.get_connection()) as db,db:
            db.execute("UPDATE files SET state='failed',error='Embedding space unavailable' WHERE id=?",(file["id"],))
            db.execute("DELETE FROM chunks WHERE file_id=?",(file["id"],))
        with patch.object(self.app.rag,"retrieve",AsyncMock(side_effect=AssertionError("No semantic access"))):
            events=self.send("Summarize this document.",use_files=True,file_ids=[file["id"]])
        self.assertEqual(events[-1]["status"],"completed",events)
        self.assertEqual(self.details()["acquisition"],"direct_complete")
        source=self.rows()[-1]["sources"][0]["id"]
        self.assertEqual(self.client.get(f"/conversations/{self.cid}/sources/{source}").status_code,200)

    def test_complete_context_never_silently_drops_sections(self):
        file=self.upload()
        sources=canonical_sources(self.app.documents,self.cid,[file])
        sources.append({**sources[0],"id":"SOURCE_"+"b"*24,"text":"long material "*5000})
        history=[{"role":"user","content":"Explain all sections."}]
        self.assertIsNone(complete_context(history,history[-1]["content"],sources,"",""))
        compact=sources[:1]
        result=complete_context(history,history[-1]["content"],compact,"","")
        check(result[0]);self.assertEqual(result[1],compact)

    def test_selected_section_and_file_scope_are_preserved(self):
        file=self.upload("guide.md",b"# One\n\nFirst fact.\n\n# Two\n\nSecond fact.","text/markdown")
        sources=canonical_sources(self.app.documents,self.cid,[file],"Two")
        self.assertNotIn("First fact",str(sources))
        self.assertIn("Second fact",str(sources))
        with self.assertRaisesRegex(ValueError,"scope"):
            bundle_metadata([],sources,"direct_complete")
        events=self.send("Summarize section Two",use_files=True,file_ids=[file["id"]])
        self.assertEqual(events[-1]["status"],"completed",events)
        self.assertNotIn("First fact",str(self.calls[-1]))
        self.assertEqual(self.details()["coverage_manifest"]["required_sections"][0]["location"],{"section":"Two"})

    def test_coverage_requires_substantive_output_and_does_not_claim_proof(self):
        a="SOURCE_"+"a"*24;b="SOURCE_"+"b"*24
        manifest={"required_sections":[{"id":"one","source_ids":[a]},{"id":"two","source_ids":[b]}]}
        result=answer_coverage(f"# One [{a}]\n\nA supported explanation of a pump. [{a}]\n\n[{b}]",manifest)
        self.assertEqual((result["linked"],result["missing"]),(1,["two"]))
        self.assertIsNone(result["semantic_complete"])
        self.assertGreater(result["map"][0]["portions"][0]["end"],0)

    def test_task_changes_invalidate_summary_cache_and_reduction_keeps_objective(self):
        file=self.upload()
        seen=[]
        class Client:
            async def list(inner):return {"models":[{"model":"llama3.2:3b","digest":"task-model-v1"}]}
            async def chat(inner,**kwargs):
                seen.append(kwargs)
                return {"message":{"content":"Compact source notes." if kwargs["options"]["num_predict"]<900 else "Source evidence and qualifications. "*50}}
            async def close(inner):pass
        async def run():
            task=task_state("Teach this like I am eight, with an analogy.",[file])
            with patch.object(self.app.provider,"client",return_value=Client()):
                _,_,info=await self.app.document_agent.summarizer.summarize(self.cid,[file],lambda _:None,task=task,final_budget=350)
                self.assertGreater(info["final_reductions"],0)
                count=len(seen)
                await self.app.document_agent.summarizer.summarize(self.cid,[file],lambda _:None,task=task,final_budget=350)
                self.assertEqual(len(seen),count)
                await self.app.document_agent.summarizer.summarize(self.cid,[file],lambda _:None,task=task_state("Critique the assumptions for an engineer.",[file]),final_budget=350)
                self.assertGreater(len(seen),count)
            for call in seen[:count]:
                self.assertIn("with an analogy",str(call["messages"]))
                check(call["messages"],call["options"]["num_predict"])
        asyncio.run(run())

    def test_huge_task_rejected_before_model_inference(self):
        file=self.upload()
        async def run():
            with patch.object(self.app.provider,"client",side_effect=AssertionError("No model call")):
                with self.assertRaisesRegex(ValueError,"insufficient context"):
                    await self.app.document_agent.summarizer.summarize(self.cid,[file],lambda _:None,task={"objective":"x"*50000})
        asyncio.run(run())

    def test_partial_coverage_warning_is_emitted_and_persisted(self):
        file=self.upload()
        with closing(self.app.get_connection()) as db,db:
            db.execute("UPDATE files SET coverage=? WHERE id=?",(json.dumps({"complete":False,"unreadable_pages":[2]}),file["id"]))
        events=self.send("Summarize the complete document.",use_files=True,file_ids=[file["id"]])
        emitted="".join(e["content"] for e in events if e["type"]=="delta")
        self.assertEqual(emitted,self.rows()[-1]["content"])
        self.assertIn("Source extraction is partial",emitted)

    def test_curly_quotes_are_verified_as_quotes_not_question_marks(self):
        from RAG.grounding import deterministic
        file=self.upload()
        sources=canonical_sources(self.app.documents,self.cid,[file])
        sid=sources[0]["id"]
        result={"sources":sources,"tools":[]}
        self.assertTrue(deterministic(self.app.documents,self.cid,'\u201cInvented quoted sentence\u201d ['+sid+']',result))
        self.assertFalse(deterministic(self.app.documents,self.cid,'?Not a quotation? ['+sid+']',result))

    def test_output_sections_are_not_document_section_selectors(self):
        from RAG.document_agent import rules
        question="Summarize this whole lesson for an eight-year-old, with one short section for each stage."
        self.assertEqual(rules(question).route,"SUMMARIZE_DOCUMENT")
        self.assertEqual(rules("Summarize section Introduction").section,"Introduction")
        self.assertEqual(rules("Please summarize section Introduction").section,"Introduction")

    def test_ollama_usage_survives_streaming_and_missing_counts_stay_unknown(self):
        from RAG.request_state import RequestState,current
        from RAG.model_provider import ModelProvider
        class Client:
            async def chat(inner,**kwargs):
                if not kwargs.get("stream"):
                    return {"message":{"content":"ok"},"prompt_eval_count":10,"eval_count":2}
                async def chunks():
                    yield {"message":{"content":"text"},"done":False}
                    yield {"message":{"content":""},"done":True,"prompt_eval_count":12,"eval_count":3}
                return chunks()
            async def close(inner):pass
        async def run():
            state=RequestState();token=current.set(state)
            try:
                with patch("RAG.model_provider.ollama.AsyncClient",return_value=Client()):
                    client=ModelProvider().client()
                    await client.chat(messages=[],stream=False)
                    stream=await client.chat(messages=[],stream=True)
                    async for _ in stream:pass
                    await client.close()
                self.assertEqual([x["eval_count"] for x in state.report()["model_token_usage"]],[2,3])
                self.assertNotIn("eval_duration",state.report()["model_token_usage"][0])
            finally:current.reset(token)
        asyncio.run(run())
