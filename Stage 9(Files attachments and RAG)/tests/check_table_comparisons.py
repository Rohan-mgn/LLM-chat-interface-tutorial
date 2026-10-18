"""Derived arithmetic, incomplete-data safeguards and actual chat/tool boundaries."""
import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from RAG.table_tools import analyze
from RAG.document_agent import TableSpec
import check_rag as fixtures


def blocks(values=("100", "125")):
    return [{"id":f"BLOCK_{i}","type":"row","table":"CSV","headers":["Period","Amount"],
             "row":i+2,"cells":{"Period":f"Q{i+1}","Amount":v}} for i,v in enumerate(values)]


def specification():
    return {"table":"CSV","group_by":["Period"],"filters":[],"aggregates":[{"op":"sum","column":"Amount"}],
        "comparisons":[{"aggregate":0,"baseline":{"Period":"Q1"},"current":{"Period":"Q2"},
                        "metrics":["difference","percent_change","ratio"]}]}


class ArithmeticTests(unittest.TestCase):
    def test_exact_decimal_change_and_source_rows(self):
        result=analyze(blocks(("0.1","0.3")),specification())
        metrics=result["comparisons"][0]["metrics"]
        self.assertEqual(metrics["difference"]["value"],"0.2")
        self.assertEqual(metrics["percent_change"]["value"],"200.000000")
        self.assertEqual(metrics["ratio"]["value"],"3.000000")
        self.assertFalse(metrics["percent_change"]["rounded"])
        self.assertEqual(result["results"][0]["source_rows"]["ids"],["BLOCK_0"])

    def test_repeating_ratio_preserves_fraction_and_rounding(self):
        result=analyze(blocks(("3","4")),specification())["comparisons"][0]
        value=result["metrics"]["percent_change"]
        self.assertEqual(value["value"],"33.333333")
        self.assertTrue(value["rounded"])
        self.assertEqual(value["exact_fraction"],{"numerator":"100","denominator":"3"})

    def test_comparisons_use_exact_averages_before_rounding(self):
        rows=blocks(("1","1"))
        rows += [{**rows[0],"id":str(i),"cells":{"Period":"Q1","Amount":"0"}} for i in range(2)]
        spec=specification();spec["aggregates"][0]["op"]="average"
        result=analyze(rows,spec)["comparisons"][0]["metrics"]
        self.assertEqual(result["percent_change"]["exact_fraction"],{"numerator":"200","denominator":"1"})
        self.assertFalse(result["percent_change"]["rounded"])
        self.assertEqual(result["difference"]["exact_fraction"],{"numerator":"2","denominator":"3"})

    def test_zero_and_negative_baselines_are_explicit(self):
        result=analyze(blocks(("0","5")),specification())["comparisons"][0]
        self.assertEqual(result["metrics"]["difference"]["value"],"5")
        self.assertIsNone(result["metrics"]["percent_change"]["value"])
        self.assertEqual(result["metrics"]["ratio"]["status"],"undefined_zero_baseline")
        result=analyze(blocks(("-100","-50")),specification())["comparisons"][0]
        self.assertEqual(result["metrics"]["percent_change"]["value"],"-50.000000")
        self.assertIn("/ baseline",result["formula"])

    def test_missing_group_is_not_zero(self):
        result=analyze(blocks(("2",)),specification())["comparisons"][0]
        self.assertEqual(result["status"],"missing_group")
        self.assertEqual(result["metrics"],{})

    def test_partial_sum_cannot_be_compared_or_ranked(self):
        for invalid in (None,"",True,"unknown","1e9999"):
            with self.subTest(invalid=invalid):
                rows=blocks()+[{**blocks()[0],"id":"BLOCK_bad","cells":{"Period":"Q1","Amount":invalid}}]
                spec=specification();spec["ranking"]={"aggregate":0,"direction":"descending","limit":1}
                result=analyze(rows,spec)
                self.assertEqual(result["comparisons"][0]["status"],"incomplete_numeric_values")
                self.assertEqual(result["ranking"]["entries"],[])

    def test_formula_without_cache_cannot_be_compared(self):
        rows=blocks((None,"2"));rows[0]["formulas"]={"Amount":"=1+1"}
        with self.assertRaisesRegex(ValueError,"no cached value"):
            analyze(rows,specification())

    def test_filter_is_applied_before_comparison(self):
        rows=blocks()+[{**blocks()[0],"id":"excluded","cells":{"Period":"Q1","Amount":"999"}}]
        spec=specification();spec["filters"]=[{"column":"Amount","op":"lt","value":200}]
        result=analyze(rows,spec)
        self.assertEqual(result["comparisons"][0]["metrics"]["difference"]["value"],"25")
        self.assertEqual(result["matched_rows"],2)

    def test_rank_ties_and_ascending_order(self):
        spec=specification();spec["comparisons"]=[];spec["ranking"]={"aggregate":0,"direction":"descending","limit":1}
        ranking=analyze(blocks(("10","20","20","5")),spec)["ranking"]
        self.assertEqual([e["group"]["Period"] for e in ranking["entries"]],["Q2","Q3"])
        self.assertEqual([e["rank"] for e in ranking["entries"]],[1,1])
        spec["ranking"].update(direction="ascending",limit=2)
        self.assertEqual([e["value"] for e in analyze(blocks(("10","20","20","5")),spec)["ranking"]["entries"]],["5","10"])

    def test_multicolumn_selectors_must_be_complete_and_exact(self):
        spec=specification();spec["group_by"].append("Department")
        rows=blocks()
        for row in rows:row["headers"].append("Department");row["cells"]["Department"]="IT"
        with self.assertRaisesRegex(ValueError,"every group"):
            analyze(rows,spec)
        for side in ("baseline","current"):spec["comparisons"][0][side]["Department"]="IT"
        self.assertEqual(analyze(rows,spec)["comparisons"][0]["status"],"ok")
        spec["comparisons"][0]["current"]["Department"]="it"
        self.assertEqual(analyze(rows,spec)["comparisons"][0]["status"],"missing_group")

    def test_untrusted_derived_arguments_are_rejected(self):
        for change in ({"aggregate":True},{"aggregate":8},{"metrics":["eval"]},{"metrics":["ratio","ratio"]},
                       {"baseline":{"Period":1}},{"sql":"DROP TABLE files"}):
            spec=specification();spec["comparisons"][0].update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):analyze(blocks(),spec)
        spec=specification();spec["comparisons"]*=9
        with self.assertRaises(ValueError):analyze(blocks(),spec)

    def test_source_row_manifest_is_bounded_but_sensitive(self):
        rows=[{**blocks()[0],"id":str(i)} for i in range(250)]
        spec=specification();spec["comparisons"]=[]
        before=analyze(rows,spec)["results"][0]["source_rows"]
        self.assertEqual(len(before["ids"]),200);self.assertTrue(before["truncated"])
        rows[-1]["id"]="changed"
        self.assertNotEqual(before["sha256"],analyze(rows,spec)["results"][0]["source_rows"]["sha256"])

    def test_schema_rejects_untrusted_options_and_legacy_spec_still_works(self):
        spec=specification();spec["ranking"]={"aggregate":0,"direction":"descending","limit":True}
        with self.assertRaises(ValueError):TableSpec.model_validate(spec)
        spec=specification();del spec["comparisons"]
        self.assertEqual(analyze(blocks(),TableSpec.model_validate(spec).model_dump())["results"][0]["values"][0]["result"],"100")


class ComparisonChatTests(unittest.TestCase):
    setUp=fixtures.Checks.setUp
    tearDown=fixtures.Checks.tearDown
    upload=fixtures.Checks.upload
    send=fixtures.Checks.send
    rows=fixtures.Checks.rows

    def test_chat_uses_deterministic_values_and_persists_tool_evidence(self):
        file=self.upload("sales.csv",b"Period,Amount\nQ1,100\nQ2,125\n","text/csv")
        with patch.object(self.app.provider,"structured",AsyncMock(return_value=specification())),patch.object(
                self.app.rag,"retrieve",AsyncMock(side_effect=AssertionError("No vector retrieval for arithmetic"))):
            events=self.send("What is the percentage change in Amount from Q1 to Q2?",use_files=True,file_ids=[file["id"]])
        self.assertEqual(events[-1]["status"],"completed",events)
        reply=self.rows()[-1]
        self.assertIn(r"25\.000000%",reply["content"])
        self.assertFalse(any(c.get("stream") for c in self.calls))
        evidence=self.client.get(f"/conversations/{self.cid}/tool-evidence/{reply['tool_evidence'][0]['id']}").json()
        self.assertEqual(evidence["exact_result"]["comparisons"][0]["baseline"],"100")
        self.assertEqual(evidence["fingerprint"],file["sha256"])

    def test_single_spreadsheet_comparison_does_not_require_two_files(self):
        file=self.upload("sales.csv",b"Period,Amount\nQ1,100\nQ2,125\n","text/csv")
        with patch.object(self.app.provider,"structured",AsyncMock(return_value=specification())):
            events=self.send("Compare Q1 and Q2 Amount in sales.csv",use_files=True,file_ids=[file["id"]])
        self.assertEqual(events[-1]["status"],"completed",events)
        self.assertTrue(self.rows()[-1]["tool_evidence"])

    def test_model_selected_filter_scope_is_visible_in_the_answer(self):
        file=self.upload("sales.csv",b"Period,Amount\nQ1,100\nQ1,unknown\nQ2,125\n","text/csv")
        spec=specification();spec["filters"]=[{"column":"Amount","op":"ne","value":"unknown"}]
        with patch.object(self.app.provider,"structured",AsyncMock(return_value=spec)):
            events=self.send("What is the percentage change in Amount from Q1 to Q2?",use_files=True,file_ids=[file["id"]])
        self.assertEqual(events[-1]["status"],"completed",events)
        text=self.rows()[-1]["content"]
        self.assertIn("Matched 2 of 3 rows",text)
        self.assertIn("Applied filters",text)
        self.assertIn("Amount ne unknown",text)
        self.assertIn("filtered subset",text)

    def test_planner_tool_uses_same_arithmetic_and_revision_boundary(self):
        from RAG.attachment_tools import AttachmentTools
        from RAG.evidence_release import EvidenceRevision, SourceInvalidated
        from contextlib import closing
        file=self.upload("sales.csv",b"Period,Amount\nQ1,3\nQ2,4\n","text/csv")
        self.send("Hello",use_files=True,file_ids=[file["id"]])
        mid=[r for r in self.rows() if r["role"]=="user"][-1]["id"]
        revision=EvidenceRevision.capture(self.app.documents,self.cid,mid,[file["id"]])
        session=AttachmentTools(self.app.documents,self.app.rag,self.app.provider,revision,"Compare periods")
        tool=next(t for t in session.catalog() if t.name=="calculate_table")
        result=asyncio.run(tool.execute({"file":"F1",**specification()}))
        self.assertEqual(result["verified_calculation"]["comparisons"][0]["metrics"]["ratio"]["value"],"1.333333")
        original=session._calculate
        async def changed(args):
            answer=await original(args)
            with self.app.documents.mutation(self.cid),closing(self.app.documents.connect()) as db,db:
                db.execute("UPDATE files SET sha256='changed' WHERE id=?",(file["id"],))
            return answer
        session._calculate=changed
        tool=next(t for t in session.catalog() if t.name=="calculate_table")
        with self.assertRaises(SourceInvalidated):asyncio.run(tool.execute({"file":"F1",**specification()}))
        self.assertEqual(len(session.provenance),1)


if __name__=="__main__":unittest.main()
