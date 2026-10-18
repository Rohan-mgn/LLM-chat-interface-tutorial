"""Production upload/chat evaluation on independently constructed rotated symbols."""
import hashlib
import io
import json
import math
from pathlib import Path
import sys
import time

STAGE=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(STAGE),str(STAGE/'tests')]


def build(spec):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject,NameObject
    writer=PdfWriter();truth=[]
    for number,objects in enumerate(spec['pages'],1):
        page=writer.add_blank_page(width=600,height=400);commands=['0.5 w']
        for obj in objects:
            x,y=obj['center'];angle=math.radians(obj['angle']);s=obj['scale'];points=[]
            for shape in [[(-16,-16),(0,0),(-16,16),(-16,-16)],[(16,-16),(0,0),(16,16),(16,-16)]]:
                converted=[(x+s*(a*math.cos(angle)-b*math.sin(angle)),y+s*(a*math.sin(angle)+b*math.cos(angle))) for a,b in shape]
                points.extend(converted)
                commands.append(' '.join(f'{a:.5f} {b:.5f} '+('m' if i==0 else 'l') for i,(a,b) in enumerate(converted))+' S')
            if obj['marked']:commands.append(f'{x-2} {y-2} 4 4 re f')
            else:
                truth.append({'page':number,'class':'gate valve','bbox':[min(a for a,b in points),400-max(b for a,b in points),max(a for a,b in points),400-min(b for a,b in points)]})
        content=DecodedStreamObject();content.set_data('\n'.join(commands).encode())
        page[NameObject('/Contents')]=writer._add_object(content)
    output=io.BytesIO();writer.write(output);return output.getvalue(),truth


def main():
    import check_rag as fixtures
    from RAG.drawing_validation import evaluate
    path=STAGE/'tests/drawing_rotations_frozen.json';spec=json.loads(path.read_text(encoding='utf8'))
    pdf,truth=build(spec);fixture=fixtures.Checks();fixture.setUp()
    try:
        file=fixture.upload('rotated-symbols.pdf',pdf,'application/pdf',ready=False)
        # This intentionally unlabeled vector drawing has no native text. Text
        # indexing may fail without OCR; drawing analysis must remain available.
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            state=fixture.app.documents.row(fixture.cid,file['id'])
            if state['state'] in ('ready','failed'):break
            time.sleep(.05)
        started=time.perf_counter()
        events=fixture.send('Count all gate valves across all pages of rotated-symbols.pdf.',use_files=True,file_ids=[file['id']])
        elapsed=time.perf_counter()-started;reply=fixture.rows()[-1]
        if events[-1]['status']!='completed':raise RuntimeError(events)
        tool=fixture.client.get(f'/conversations/{fixture.cid}/tool-evidence/{reply["tool_evidence"][0]["id"]}').json()
        result=tool['result'];metrics=evaluate(result['detections'],truth)
        passed=metrics['precision']>=spec['minimum_precision'] and metrics['recall']>=spec['minimum_recall'] and result['count']==spec['expected_count'] and result['label']==spec['expected_label']
        report={'passed':passed,'fixture_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'pdf_sha256':hashlib.sha256(pdf).hexdigest(),
            'metrics':metrics,'count':result['count'],'label':result['label'],'seconds':elapsed,'truth':truth,'detections':result['detections'],
            'answer':reply['content'],'native_text_state':state['state'],
            'limitations':'Synthetic five-target scope plus a marked lookalike; automatic vector profile only. Not exhaustive real-drawing ground truth or arbitrary symbol recognition.'}
        (STAGE/'tests/results/drawing-rotated-symbols.json').write_text(json.dumps(report,indent=2),encoding='utf8')
        print(json.dumps({k:report[k] for k in ('passed','metrics','count','label','seconds')},indent=2))
        if not passed:raise SystemExit(1)
    finally:fixture.tearDown()


if __name__=='__main__':main()
