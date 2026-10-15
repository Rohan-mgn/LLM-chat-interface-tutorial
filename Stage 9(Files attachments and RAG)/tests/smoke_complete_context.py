"""Opt-in local-model comparison; no downloads, no frontier-quality claims."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import check_rag as fixtures

CASES=[
    {"id":"water-cycle-child","document":"# Evaporation\n\nThe sun warms water in lakes. Some liquid water becomes water vapor and rises into the air.\n\n# Condensation\n\nHigher air is cooler. Water vapor becomes tiny liquid drops that form clouds.\n\n# Precipitation\n\nWhen drops become heavy, they fall as rain. The water returns to streams and lakes.\n",
     "question":"Summarize this whole lesson for an eight-year-old, with one short section for each stage and a simple analogy. Keep the stages in order.",
     "required":["evaporation","condensation","precipitation"]},
    {"id":"maintenance-study","document":"# Inspection\n\nBefore starting the pump, inspect the inlet for debris and check that the pressure gauge reads zero.\n\n# Operation\n\nOpen the inlet valve before starting the pump. Stop the pump if pressure exceeds 90 kPa.\n\n# Shutdown\n\nTurn the motor off, close the inlet valve and record the final pressure. Do not open the casing while the pump is pressurized.\n",
     "question":"Summarize this complete procedure as a three-part study guide. Include the stop condition, one practice question per part, and a short answer key.",
     "required":["90","inspect","off"]}
]

def run(case,direct):
    fixture=fixtures.Checks(methodName="test_no_evidence_does_not_hallucinate_a_model_answer")
    fixture.setUp()
    try:
        file=fixture.upload("lesson.md",case["document"].encode(),"text/markdown")
        # Fixture-only deterministic ingestion is outside the timed operation.
        # These complete-document paths must not need any embedding calls.
        fixture.mock.stop()
        started=time.perf_counter()
        with patch("RAG.document_agent.complete_context",wraps=__import__("RAG.answering",fromlist=["complete_context"]).complete_context) if direct else patch("RAG.document_agent.complete_context",return_value=None):
            events=fixture.send(case["question"],use_files=True,file_ids=[file["id"]])
        elapsed=time.perf_counter()-started
        text=fixture.rows()[-1]["content"]
        with closing(fixture.app.get_connection()) as db:
            row=db.execute("SELECT details FROM rag_runs ORDER BY message_id DESC LIMIT 1").fetchone()
        details=json.loads(row[0]) if row else {}
        return {"case":case["id"],"strategy":"direct" if direct else "forced_hierarchical_control",
            "question":case["question"],"document_sha256":hashlib.sha256(case["document"].encode()).hexdigest(),
            "status":events[-1],"errors":[e for e in events if e["type"]=="error"],"seconds":round(elapsed,3),"answer":text,
            "required_terms_present":{term:term.casefold() in text.casefold() for term in case["required"]},
            "details":details}
    finally:
        fixture.tearDown()

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live",action="store_true",required=True)
    parser.add_argument("--output",default="tests/results/complete-context-live.json")
    args=parser.parse_args()
    import ollama
    models=ollama.Client(timeout=10).list()["models"]
    if not any(m["model"]=="llama3.2:3b" for m in models):
        raise SystemExit("Existing llama3.2:3b is unavailable; no model will be downloaded.")
    report={"kind":"two development smoke scenarios, not full acceptance",
        "timing_limitations":"One run per case/strategy; regression work may overlap. Not a percentage speedup or model-quality proof.",
        "ingestion":"Isolated fixture ingestion; real configured Ollama chat model during timed answering.",
        "models":[{"model":m["model"],"digest":m["digest"]} for m in models],
        "rows":[]}
    for case in CASES:
        for direct in (False,True):
            row=run(case,direct);report["rows"].append(row)
            Path(args.output).write_text(json.dumps(report,indent=2),encoding="utf-8")
            print(row["case"],row["strategy"],row["seconds"],row["status"],flush=True)
