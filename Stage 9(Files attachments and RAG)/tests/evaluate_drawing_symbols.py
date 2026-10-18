"""Actual upload/chat count evaluation with labels withheld from production tools."""
import io
import hashlib
import json
from pathlib import Path
import sys
import time

STAGE=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(STAGE),str(STAGE/'tests')]
import check_rag as fixtures
from check_drawing_analysis import drawing
from pypdf import PdfReader,PdfWriter
from RAG.drawing_validation import evaluate


def main():
    writer=PdfWriter();source=PdfReader(io.BytesIO(drawing()))
    writer.add_page(source.pages[0]);writer.add_page(source.pages[0]);buffer=io.BytesIO();writer.write(buffer)
    file_bytes=buffer.getvalue();fixture=fixtures.Checks();fixture.setUp()
    try:
        file=fixture.upload('two-page-symbols.pdf',file_bytes,'application/pdf')
        started=time.perf_counter()
        events=fixture.send('Count all gate valves across all pages of two-page-symbols.pdf.',use_files=True,file_ids=[file['id']])
        seconds=time.perf_counter()-started;reply=fixture.rows()[-1]
        if events[-1]['status']!='completed':raise RuntimeError(events)
        tool=fixture.client.get(f'/conversations/{fixture.cid}/tool-evidence/{reply["tool_evidence"][0]["id"]}').json()
        # Ground-truth labels are loaded only after production execution finishes.
        truth_path=STAGE/'tests/drawing_synthetic_truth.json';truth=json.loads(truth_path.read_text(encoding='utf-8'))
        result=tool['result'];metrics=evaluate(result['detections'],truth['objects'])
        passed=result['label']==truth['required_label'] and result['count']==truth['required_count'] and metrics['precision']>=truth['minimum_precision'] and metrics['recall']>=truth['minimum_recall']
        report={'passed':passed,'metrics':metrics,'seconds':seconds,'answer':reply['content'],'label':result['label'],
            'count':result['count'],'coverage_complete':result['coverage_complete'],'detections':result['detections'],
            'fixture_sha256':hashlib.sha256(file_bytes).hexdigest(),'truth_sha256':hashlib.sha256(truth_path.read_bytes()).hexdigest(),
            'limitations':'Synthetic four-object scope only. Ingestion model is mocked; production chat, deterministic tools and persistence are real. No real-drawing accuracy or human review claimed.'}
        (STAGE/'tests/results/drawing-symbol-synthetic.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps({k:report[k] for k in ('passed','metrics','seconds','count','label')},indent=2))
        if not passed:raise SystemExit(1)
    finally:fixture.tearDown()


if __name__=='__main__':main()
