"""Ordinal follow-ups use completed branch answers, never document section numbers."""
from contextlib import closing
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import check_rag as fixtures
from RAG.answer_references import requested, select
from RAG.operations import resolve

class StructureTests(unittest.TestCase):
    def test_ordered_points_skip_nested_lists_code_and_quotes(self):
        text="1. First point.\n   - nested one\n   - nested two\n2. Second point with useful explanation.\n\n```text\n3. Not a point\n```\n\n> 4. Not a point\n"
        self.assertTrue(select(text,requested("Explain the second point again."))["text"].startswith("2. Second"))
        with self.assertRaises(ValueError):select(text,(3,"point"))
    def test_heading_reference_uses_answer_headings(self):
        text="# Overview\n\n## Pumps\n\nMoving water.\n\n## Valves\n\nStopping water.\n"
        chosen=select(text,requested("Explain your second heading."))
        self.assertIn("Valves",chosen["text"]);self.assertNotIn("Pumps",chosen["text"])
        self.assertIsNone(requested("Explain section 2 in the document."))
        self.assertIsNone(requested("Explain the second point in the document."))
    def test_multiple_lists_require_clarification(self):
        text="1. One\n2. Two\n\nDifferent topic:\n\n1. Three\n2. Four"
        with self.assertRaisesRegex(ValueError,"unique"):select(text,(2,"point"))
        with self.assertRaises(ValueError):requested("Explain the first point and the second point.")
    def test_nonexistent_and_oversized_references_do_not_guess(self):
        with self.assertRaises(ValueError):select("An ordinary paragraph.",(2,"point"))
        with self.assertRaises(ValueError):select("1. "+"x"*1900,(1,"point"))
        with self.assertRaises(ValueError):select("x"*25000,(1,"heading"))
    def test_bullets_are_positional_not_document_numbers(self):
        text="- First idea\n- A distinct second idea\n"
        self.assertEqual(select(text,requested("What about bullet 2?"))["text"],"- A distinct second idea")

class ReferenceIntegration(unittest.TestCase):
    setUp=fixtures.Checks.setUp
    tearDown=fixtures.Checks.tearDown
    upload=fixtures.Checks.upload
    send=fixtures.Checks.send
    rows=fixtures.Checks.rows

    def seed(self,file,body=None,question="What is the project codename?"):
        self.send(question,use_files=True,file_ids=[file["id"]])
        assistant=self.rows()[-1]
        sid=assistant["sources"][0]["id"]
        content=body or "1. Project introduction.\n2. The project codename is Blue Falcon. ["+sid+"]"
        with closing(self.app.get_connection()) as db,db:
            db.execute("UPDATE messages SET content=? WHERE id=?",(content,assistant["id"]))
        return assistant["id"]

    def details(self):
        with closing(self.app.get_connection()) as db:
            return json.loads(db.execute("SELECT details FROM rag_runs ORDER BY message_id DESC LIMIT 1").fetchone()[0])

    def test_followup_targets_answer_point_and_keeps_original_question(self):
        file=self.upload();mid=self.seed(file)
        question="Explain the second point again with an analogy."
        before=self.embeds
        with patch.object(self.app.rag,"retrieve",AsyncMock(side_effect=AssertionError("Known source needs no retrieval"))):
            events=self.send(question,use_files=True,file_ids=[file["id"]])
        self.assertEqual(self.embeds,before)
        self.assertEqual(events[-1]["status"],"completed",events)
        details=self.details()
        self.assertEqual(details["answer_reference"]["message_id"],mid)
        self.assertEqual(details["answer_reference"]["ordinal"],2)
        self.assertTrue(details["answer_reference"]["unverified"])
        self.assertEqual(details["acquisition"],"direct_answer_reference")
        self.assertIn("Blue Falcon",str(self.calls[-1]["messages"]))
        self.assertEqual(self.calls[-1]["messages"][-1]["content"],question)
        self.assertIn("not a numbered document section",str(self.calls[-1]["messages"]))

    def test_summarize_point_does_not_become_whole_document_summary(self):
        file=self.upload();self.seed(file)
        with patch.object(self.app.document_agent.summarizer,"summarize",AsyncMock(side_effect=AssertionError("Not whole document"))):
            events=self.send("Summarize the second point again.",use_files=True,file_ids=[file["id"]])
        self.assertEqual(events[-1]["status"],"completed",events)
        self.assertEqual(self.details()["route"],"DOCUMENT_QA")

    def test_reference_uses_parent_branch_not_newest_sibling(self):
        file=self.upload();first=self.seed(file)
        user=self.rows()[0]["id"]
        self.send("What is this project?",action="edit",message_id=user,use_files=True,file_ids=[file["id"]])
        sibling=self.rows()[-1]["id"]
        with closing(self.app.get_connection()) as db,db:
            db.execute("UPDATE messages SET content=? WHERE id=?",("1. Sibling\n2. Unrelated sibling claim.",sibling))
        events=self.send("Explain the second point.",parent_id=first,use_files=True,file_ids=[file["id"]])
        self.assertEqual(events[-1]["status"],"completed",events)
        self.assertEqual(self.details()["answer_reference"]["message_id"],first)
        self.assertNotIn("sibling",self.details()["answer_reference"]["text"])

    def test_removed_citation_is_not_redirected_to_current_content(self):
        file=self.upload();mid=self.seed(file)
        with closing(self.app.get_connection()) as db,db:
            db.execute("DELETE FROM citations WHERE message_id=?",(mid,))
            db.execute("DELETE FROM block_citations WHERE message_id=?",(mid,))
        with self.assertRaisesRegex(ValueError,"unavailable"):
            resolve(self.app.documents,{"conversation_id":self.cid,"user_message_id":mid},
                    "Explain the second point.",[file])

    def test_changed_or_deselected_file_cannot_supply_old_answer_evidence(self):
        file=self.upload();mid=self.seed(file)
        job={"conversation_id":self.cid,"user_message_id":mid}
        with self.assertRaisesRegex(ValueError,"no longer selected"):
            resolve(self.app.documents,job,"Explain the second point.",[])
        changed={**file,"sha256":"changed"}
        with self.assertRaisesRegex(ValueError,"changed"):
            resolve(self.app.documents,job,"Explain the second point.",[changed])

    def test_exact_operation_is_never_downgraded_to_approximate_retrieval(self):
        file=self.upload("count.txt",b"falcon falcon")
        self.send("How many times does falcon appear?",use_files=True,file_ids=[file["id"]])
        mid=self.rows()[-1]["id"]
        with self.assertRaisesRegex(ValueError,"exact calculation"):
            resolve(self.app.documents,{"conversation_id":self.cid,"user_message_id":mid},
                    "Explain the first point.",[file])

    def test_original_section_scope_is_preserved_without_using_answer_ordinal(self):
        file=self.upload("sections.md",b"# Original\n\nThe project codename is Blue Falcon.\n\n# Two\n\nUnrelated lunar weather.","text/markdown")
        mid=self.seed(file)
        with closing(self.app.get_connection()) as db,db:
            details=json.loads(db.execute("SELECT details FROM rag_runs WHERE message_id=?",(mid,)).fetchone()[0])
            details["operation"]["route"].update(route="SUMMARIZE_SECTION",section="Original")
            db.execute("UPDATE rag_runs SET details=? WHERE message_id=?",(json.dumps(details),mid))
        seen=[];original=self.app.rag.retrieve
        async def retrieve(*args,**kwargs):
            seen.append(kwargs["scope_blocks"])
            return await original(*args,**kwargs)
        from RAG.context_budget import evidence_messages
        def fallback(*args,**kwargs):
            return None if kwargs.get("require_all") else evidence_messages(*args,**kwargs)
        with patch.object(self.app.rag,"retrieve",side_effect=retrieve),patch("RAG.document_agent.evidence_messages",side_effect=fallback):
            events=self.send("Explain the second point.",use_files=True,file_ids=[file["id"]])
        self.assertEqual(events[-1]["status"],"completed",events)
        selected={b["id"] for b in self.app.documents.blocks(self.cid,file["id"]) if b.get("section")=="Original"}
        self.assertEqual(seen[0],selected)
        self.assertEqual(self.details()["operation"]["route"]["section"],"Original")

    def test_explicit_filename_is_not_interpreted_as_previous_answer(self):
        file=self.upload()
        self.assertIsNone(resolve(self.app.documents,{}, "Explain the second point in facts.txt.",[file]))

    def test_uncited_prior_point_uses_current_retrieval_instead_of_trusting_history(self):
        file=self.upload()
        self.seed(file,body="1. Introduction.\n2. The project codename is Blue Falcon.")
        events=self.send("Explain the second point.",use_files=True,file_ids=[file["id"]])
        self.assertEqual(events[-1]["status"],"completed",events)
        self.assertEqual(self.details()["acquisition"],"hybrid_retrieval")
        self.assertIn("Blue Falcon",self.details()["rewritten_query"])

    def test_known_canonical_citation_remains_readable_with_index_unavailable(self):
        file=self.upload()
        self.seed(file,question="Summarize this document.")
        with closing(self.app.get_connection()) as db,db:
            db.execute("UPDATE files SET state='failed',error='Index unavailable' WHERE id=?",(file["id"],))
            db.execute("DELETE FROM chunks WHERE file_id=?",(file["id"],))
        with patch.object(self.app.rag,"retrieve",AsyncMock(side_effect=AssertionError("No index required"))):
            events=self.send("Explain the second point.",use_files=True,file_ids=[file["id"]])
        self.assertEqual(events[-1]["status"],"completed",events)
        self.assertEqual(self.details()["acquisition"],"direct_answer_reference")

    def test_known_source_and_retrieval_send_identical_generation_prompts(self):
        file=self.upload()
        self.seed(file)
        question="Explain the second point again with an analogy."
        self.send(question,use_files=True,file_ids=[file["id"]])
        direct=self.calls[-1]["messages"]
        assistant=self.rows()[-1]["id"]
        from RAG.context_budget import evidence_messages
        def fallback(*args,**kwargs):
            return None if kwargs.get("require_all") else evidence_messages(*args,**kwargs)
        with patch("RAG.document_agent.evidence_messages",side_effect=fallback):
            events=self.send(question,action="regenerate",message_id=assistant,use_files=True,file_ids=[file["id"]])
        self.assertEqual(events[-1]["status"],"completed",events)
        self.assertEqual(self.details()["acquisition"],"hybrid_retrieval")
        self.assertEqual(self.calls[-1]["messages"],direct)


class CompleteEvidenceBudget(unittest.TestCase):
    def test_required_sources_are_all_or_nothing_with_shared_formatter(self):
        from RAG.context_budget import evidence_messages
        source={"id":"SOURCE_"+"a"*24,"file_id":"f","original_filename":"a.txt",
                "text":"x"*30000,"metadata":{},"score":1}
        history=[{"role":"user","content":"Explain this point."}]
        self.assertIsNone(evidence_messages(history,history[-1]["content"],[source],"","",require_all=True))
        source["text"]="Known authorized passage."
        required=evidence_messages(history,history[-1]["content"],[source],"","",require_all=True)
        regular=evidence_messages(history,history[-1]["content"],[source],"","")
        self.assertEqual(required,regular)
