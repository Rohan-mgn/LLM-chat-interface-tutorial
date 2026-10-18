"""Frozen real-upload production-chat probe; automated checks are not human QA."""
import argparse
from contextlib import closing
import hashlib
import importlib.util
import json
import re
from pathlib import Path
import statistics
import sys
import time
from uuid import uuid4

STAGE=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--application',type=Path,default=STAGE)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--fixture',type=Path,default=STAGE/'tests/drawing_qa_frozen.json')
    parser.add_argument('--live',action='store_true',required=True)
    args=parser.parse_args();app_path=args.application.resolve()
    sys.path.insert(0,str(app_path))
    from fastapi.testclient import TestClient
    import ollama
    models=ollama.Client(timeout=10).list()['models']
    if not any(m['model']=='llama3.2:3b' for m in models):raise RuntimeError('Existing answering model unavailable; no downloads.')
    spec=importlib.util.spec_from_file_location('drawing_qa_application',app_path/'main.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    root=STAGE/'tests/.artifacts'/('drawing-qa-'+uuid4().hex)
    module.DATA_DIR=root;module.DATABASE=root/'chat.db'
    fixture=args.fixture;cases=json.loads(fixture.read_text(encoding='utf-8'))
    report={'complete':False,'fixture_sha256':hashlib.sha256(fixture.read_bytes()).hexdigest(),
            'application':str(app_path),'main_sha256':hashlib.sha256((app_path/'main.py').read_bytes()).hexdigest(),
            'models':[{'model':m['model'],'digest':m['digest']} for m in models],
            'limitations':cases.get('limitations','Seven development probes with fixed string checks, not independently human-reviewed answer quality or symbol ground truth. Latency is one observation per case.'),
            'rows':[],'artifacts':str(root),'ingestion':[]}
    def save():args.output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    with TestClient(module.app) as client:
        cid=client.post('/conversations').json()['id'];ids=[]
        documents=cases.get('documents',[{'path':str(STAGE.parent/'Docs'/name)} for name in ('page_14.pdf','page_15.pdf')])
        for document in documents:
            start=time.perf_counter();path=Path(document['path']);name=path.name
            if document.get('sha256') and hashlib.sha256(path.read_bytes()).hexdigest()!=document['sha256']:raise RuntimeError('Frozen document hash changed.')
            response=client.post(f'/conversations/{cid}/files',files={'upload':(name,path.read_bytes(),'application/pdf')})
            response.raise_for_status();fid=response.json()['file']['id'];ids.append(fid)
            deadline=time.monotonic()+240
            while time.monotonic()<deadline:
                row=module.documents.row(cid,fid)
                if row['state'] in ('ready','failed'):break
                time.sleep(.2)
            report['ingestion'].append({'name':name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'state':row['state'],'seconds':time.perf_counter()-start})
            save()
            if row['state']!='ready':raise RuntimeError(row.get('error') or 'Ingestion deadline')
        for case in cases['cases']:
            start=time.perf_counter()
            response=client.post('/chat',json={'conversation_id':cid,'generation_id':str(uuid4()),'message':case['question'],'use_files':True,'file_ids':ids})
            events=[json.loads(p[6:]) for p in response.text.strip().split('\n\n')]
            tree=client.get(f'/conversations/{cid}/tree').json()['messages'];reply=tree[-1]
            with closing(module.get_connection()) as db:
                row=db.execute('SELECT details FROM rag_runs WHERE message_id=?',(reply['id'],)).fetchone()
            answer=reply['content'];content_match=all(v.casefold() in answer.casefold() for v in case.get('expected',[])) and all(re.search(v,answer,re.I) for v in case.get('required_patterns',[]))
            resolved=[]
            for source in reply.get('sources',[]):
                lookup=client.get(f'/conversations/{cid}/sources/{source["id"]}')
                resolved.append({'id':source['id'],'status':lookup.status_code})
            pages=set()
            for source in reply.get('sources',[]):
                metadata=source['metadata'];metadata=json.loads(metadata) if isinstance(metadata,str) else metadata
                if metadata.get('page'):pages.add(metadata['page'])
            page_recall=sum(bool(pages.intersection(group)) for group in case.get('required_page_groups',[]))
            page_count=len(case.get('required_page_groups',[]))
            passed=bool(events[-1].get('status')=='completed' and content_match and page_recall==page_count and all(r['status']==200 for r in resolved))
            report['rows'].append({'question':case['question'],'expected':case.get('expected',[]),'passed':passed,'answer':answer,
                'content_match':bool(content_match),'required_page_groups_found':page_recall,'required_page_groups':page_count,'cited_pages':sorted(pages),'citation_resolution':resolved,
                'seconds':time.perf_counter()-start,'events':events,'sources':reply.get('sources',[]),'tools':reply.get('tool_evidence',[]),
                'trace':json.loads(row[0]) if row else {}})
            save();print(case['question'],passed,round(report['rows'][-1]['seconds'],2),flush=True)
    timings=sorted(r['seconds'] for r in report['rows'])
    report.update(complete=True,passed=sum(r['passed'] for r in report['rows']),total=len(report['rows']),p50_seconds=statistics.median(timings),p95_seconds=timings[-1])
    save()

if __name__=='__main__':main()
