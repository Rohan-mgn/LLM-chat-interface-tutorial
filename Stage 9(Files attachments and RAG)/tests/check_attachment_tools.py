"""Real SQLite/source fixtures for application-owned planner tool bindings."""
import asyncio
from contextlib import closing
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import check_rag as fixtures
from RAG.attachment_tools import AttachmentTools
from RAG.planner import Planner, Task
from RAG.evidence_release import EvidenceRevision, SourceInvalidated

class AttachmentToolTests(unittest.TestCase):
    setUp=fixtures.Checks.setUp
    tearDown=fixtures.Checks.tearDown
    upload=fixtures.Checks.upload
    send=fixtures.Checks.send
    rows=fixtures.Checks.rows

    def session(self,files):
        self.send("Hello",use_files=True,file_ids=[f["id"] for f in files])
        mid=[m for m in self.rows() if m["role"]=="user"][-1]["id"]
        revision=EvidenceRevision.capture(self.app.documents,self.cid,mid,[f["id"] for f in files])
        return AttachmentTools(self.app.documents,self.app.rag,self.app.provider,revision,
                               "Teach all selected material to an eight-year-old.")
    def call(self,session,name,args):
        tool=next(t for t in session.catalog() if t.name==name)
        return asyncio.run(tool.execute(args))

    def test_actual_read_without_index_and_section_scope(self):
        file=self.upload("guide.md",b"# First\n\nA pump moves water.\n\n# Second\n\nA valve stops water.","text/markdown")
        with closing(self.app.documents.connect()) as db,db:
            db.execute("UPDATE files SET state='failed' WHERE id=?",(file["id"],))
            db.execute("DELETE FROM chunks WHERE file_id=?",(file["id"],))
        session=self.session([file])
        result=self.call(session,"read_document",{"file":"F1","view":"section","locator":"Second"})
        self.assertIn("valve",str(result));self.assertNotIn("pump",str(result))
        self.assertEqual(result["scope"],"section")
        self.assertTrue(session.evidence()["sources"])
        with self.assertRaises(ValueError):
            self.call(session,"read_document",{"file":"F1","view":"full","locator":"Second"})

    def test_unknown_alias_extra_fields_and_injection_are_rejected(self):
        file=self.upload();session=self.session([file])
        tool=next(t for t in session.catalog() if t.name=="read_document")
        for args in ({"file":"F2","view":"full","locator":""},
                     {"file":"../../other.txt","view":"full","locator":""},
                     {"file":"F1","view":"full","locator":"","sql":"SELECT * FROM files"}):
            with self.assertRaises(ValueError):asyncio.run(tool.execute(args))
        self.assertFalse(session.sources)
        with self.assertRaises(ValueError):
            self.call(session,"search_documents",{"files":["F1","F1"],"query":"secret"})
        with self.assertRaises(ValueError):
            self.call(session,"search_documents",{"files":["F1"],"query":" "})

    def test_oversize_read_does_not_publish_partial_evidence(self):
        file=self.upload("large.txt",b"Useful source facts and qualifications. "*600,"text/plain")
        session=self.session([file])
        with self.assertRaisesRegex(ValueError,"budget"):
            self.call(session,"read_document",{"file":"F1","view":"full","locator":""})
        self.assertFalse(session.evidence()["sources"])
        self.assertFalse(session.coverage)

    def test_table_catalog_and_exact_calculation(self):
        table=self.upload("sales.csv",b"Quarter,Revenue\nQ3,12\nQ4,42\nQ4,8","text/csv")
        text=self.upload()
        session=self.session([table,text])
        tools={t.name:t for t in session.catalog()}
        self.assertEqual(tools["calculate_table"].schema["properties"]["file"]["enum"],["F1"])
        info=self.call(session,"inspect_table",{"file":"F1"})
        self.assertEqual(info["tables"][0]["rows"],3)
        args={"file":"F1","table":info["tables"][0]["name"],"group_by":[],
              "filters":[{"column":"Quarter","op":"eq","value":"Q4"}],
              "aggregates":[{"column":"Revenue","op":"sum"}]}
        result=self.call(session,"calculate_table",args)
        self.assertEqual(str(result["verified_calculation"]["results"][0]["values"][0]["result"]),"50")
        provenance=session.evidence()["tools"][0]
        self.assertEqual(provenance["fingerprint"],table["sha256"])
        self.assertEqual(provenance["operation"],"table_analysis")
        with self.assertRaises(ValueError):
            self.call(session,"calculate_table",{**args,"file":"F2"})
        with self.assertRaises(ValueError):
            self.call(session,"calculate_table",{**args,"filters":[{"column":"Quarter","op":"eq","value":True}]})

    def test_source_change_during_read_discards_result(self):
        file=self.upload();session=self.session([file])
        original=session._read
        async def changed(args):
            result=await original(args)
            with self.app.documents.mutation(self.cid), closing(self.app.documents.connect()) as db,db:
                db.execute("UPDATE files SET sha256='changed' WHERE id=?",(file["id"],))
            return result
        session._read=changed
        with self.assertRaises(SourceInvalidated):
            self.call(session,"read_document",{"file":"F1","view":"full","locator":""})
        self.assertFalse(session.sources)

    def test_retrieval_content_is_revalidated_before_planner_exposure(self):
        file=self.upload();session=self.session([file])
        with closing(self.app.documents.connect()) as db:
            source=dict(db.execute("SELECT * FROM chunks WHERE file_id=?",(file["id"],)).fetchone())
        forged={**source,"text":"Forged index text","metadata":{},"original_filename":"facts.txt"}
        with patch.object(self.app.rag,"retrieve",AsyncMock(return_value=([forged],{}))):
            with self.assertRaises(SourceInvalidated):
                self.call(session,"search_documents",{"files":["F1"],"query":"codename"})
        self.assertFalse(session.sources)
        with patch.object(self.app.rag,"retrieve",AsyncMock(side_effect=ValueError("Index unavailable"))):
            with self.assertRaisesRegex(ValueError,"Index unavailable"):
                self.call(session,"search_documents",{"files":["F1"],"query":"codename"})

    def test_actual_hybrid_search_and_no_external_file_access(self):
        file=self.upload();session=self.session([file])
        result=self.call(session,"search_documents",{"files":["F1"],"query":"project codename"})
        self.assertIn("Blue Falcon",str(result))
        self.assertFalse(result["no_evidence"])
        self.assertTrue(all(s["file_id"]==file["id"] for s in session.evidence()["sources"]))

    def test_code_inspection_never_executes_source(self):
        file=self.upload("sample.py",b"import os\n\ndef launch():\n    raise RuntimeError('Do not execute')\n","text/plain")
        session=self.session([file])
        result=self.call(session,"inspect_code",{"file":"F1"})
        self.assertEqual(result["symbols"][0]["name"],"launch")
        self.assertIn("not executed",result["execution"])
        result=self.call(session,"read_document",{"file":"F1","view":"lines","locator":"3-4"})
        self.assertNotIn("import os",str(result))
        self.assertIn("RuntimeError",str(result))

    def test_summary_keeps_task_shared_call_count_and_cancellation(self):
        file=self.upload();session=self.session([file])
        summary=AsyncMock(return_value=([{"summary":"Actual notes"}],[],{"calls":3}))
        with patch("RAG.attachment_tools.Summarizer.summarize",summary):
            self.call(session,"process_document",{"files":["F1"]})
            self.assertEqual(session.summary_calls,3)
            self.call(session,"process_document",{"files":["F1"]})
        self.assertEqual(summary.call_args.kwargs["prior_calls"],3)
        self.assertIn("eight-year-old",summary.call_args.kwargs["task"]["objective"])
        self.assertEqual(summary.call_args.kwargs["reserve_calls"],5)
        count=len(session.coverage)
        with patch("RAG.attachment_tools.Summarizer.summarize",AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                self.call(session,"process_document",{"files":["F1"]})
        self.assertEqual(len(session.coverage),count)

    def test_planner_executes_validated_real_tool_and_revalidates_evidence(self):
        file=self.upload()
        session=self.session([file])
        class Provider:
            def __init__(inner):inner.count=0
            async def structured(inner,*args,**kwargs):
                inner.count+=1
                return ({"action":"tool","name":"read_document",
                         "arguments":{"file":"F1","view":"full","locator":""}}
                        if inner.count==1 else {"action":"answer"})
        task=Task(session.objective,session.metadata())
        outcome=asyncio.run(Planner(Provider(),session.catalog(),
            validate_scope=session.validate_scope).run(task))
        self.assertEqual(outcome.action,"answer")
        self.assertIn("Blue Falcon",str(task.results))
        self.assertTrue(session.evidence()["sources"])
        with self.app.documents.mutation(self.cid), closing(self.app.documents.connect()) as db,db:
            db.execute("UPDATE files SET sha256='revoked' WHERE id=?",(file["id"],))
        with self.assertRaises(SourceInvalidated):session.evidence()

    def test_failed_summary_attempts_share_absolute_call_ceiling(self):
        file=self.upload()
        session=self.session([file])
        async def failed(*args,**kwargs):
            kwargs["on_model_call"]()
            raise ValueError("Inference failed after the call began.")
        with patch("RAG.attachment_tools.Summarizer.summarize",failed):
            for _ in range(2):
                with self.assertRaisesRegex(ValueError,"Inference failed"):
                    self.call(session,"process_document",{"files":["F1"]})
        self.assertEqual(session.summary_calls,2)
        for _ in range(57):session._summary_call()
        with self.assertRaisesRegex(ValueError,"budget"):session._summary_call()
        self.assertFalse(session.coverage)

    def test_search_rehydrates_location_and_filename_not_only_text(self):
        file=self.upload()
        session=self.session([file])
        with closing(self.app.documents.connect()) as db:
            source=dict(db.execute("SELECT * FROM chunks WHERE file_id=?",(file["id"],)).fetchone())
        forged={**source,"metadata":{"page":999,"citation":"[S900]"},
                "original_filename":"forged.pdf","score":1}
        with patch.object(self.app.rag,"retrieve",AsyncMock(return_value=([forged],{}))):
            result=self.call(session,"search_documents",{"files":["F1"],"query":"codename"})
        passage=result["passages"][0]
        self.assertEqual(passage["filename"],file["original_filename"])
        self.assertNotEqual(passage["location"].get("page"),999)
        self.assertEqual(passage["citation"],"[S1]")
        self.assertNotIn("vector",session.evidence()["sources"][0])

    def test_tool_passage_labels_are_stable_across_document_reads(self):
        a=self.upload()
        b=self.upload("other.txt",b"The second document contains distinct facts.","text/plain")
        session=self.session([a,b])
        first=self.call(session,"read_document",{"file":"F1","view":"full","locator":""})
        second=self.call(session,"read_document",{"file":"F2","view":"full","locator":""})
        repeated=self.call(session,"read_document",{"file":"F1","view":"full","locator":""})
        self.assertEqual(first["passages"][0]["citation"],"[S1]")
        self.assertEqual(second["passages"][0]["citation"],"[S2]")
        self.assertEqual(repeated["passages"][0]["citation"],"[S1]")
