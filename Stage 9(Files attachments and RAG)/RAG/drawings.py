"""Versioned drawing jobs and evidence, sharing the existing source mutation gate."""
import asyncio
import base64
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from uuid import uuid4
from .drawing_extract import VERSION, identifier
from .drawing_geometry import valid_box, tiles, contains, iou
from .drawing_symbols import builtin_templates, count_label, deduplicate, match_vectors
from .drawing_validation import coordinates, evaluate
from .evidence_release import gate_for, SourceInvalidated

SCHEMA=[
    "CREATE TABLE IF NOT EXISTS drawing_state(file_id TEXT PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE, revision INTEGER NOT NULL DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS drawing_runs(id TEXT PRIMARY KEY,file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,source_hash TEXT NOT NULL,page INTEGER NOT NULL,version TEXT NOT NULL,status TEXT NOT NULL,data TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS drawing_run_scope ON drawing_runs(file_id,source_hash,page,version)",
    "CREATE TABLE IF NOT EXISTS drawing_templates(id TEXT PRIMARY KEY,file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,source_hash TEXT NOT NULL,data TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS drawing_reviews(id TEXT PRIMARY KEY,file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,source_hash TEXT NOT NULL,published INTEGER NOT NULL DEFAULT 0,data TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS drawing_validations(id TEXT PRIMARY KEY,file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,data TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS drawing_artifacts(id TEXT PRIMARY KEY,file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,stored_filename TEXT NOT NULL UNIQUE,sha256 TEXT NOT NULL)",
    "CREATE TRIGGER IF NOT EXISTS drawing_artifact_unlink AFTER DELETE ON drawing_artifacts BEGIN INSERT OR IGNORE INTO pending_unlinks VALUES(OLD.stored_filename); END",
]
from .drawing_annotations import SCHEMA as ANNOTATION_SCHEMA
SCHEMA.extend([ANNOTATION_SCHEMA,
    "CREATE TABLE IF NOT EXISTS drawing_count_runs(id TEXT PRIMARY KEY,file_id TEXT REFERENCES files(id) ON DELETE CASCADE,source_hash TEXT NOT NULL,data TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS drawing_template_sources(template_id TEXT REFERENCES drawing_templates(id) ON DELETE CASCADE,source_file_id TEXT REFERENCES files(id) ON DELETE CASCADE,source_hash TEXT NOT NULL,PRIMARY KEY(template_id,source_file_id))",
    "CREATE TRIGGER IF NOT EXISTS drawing_reference_removed BEFORE DELETE ON drawing_template_sources BEGIN UPDATE drawing_state SET revision=revision+1 WHERE file_id=(SELECT file_id FROM drawing_templates WHERE id=OLD.template_id); DELETE FROM drawing_templates WHERE id=OLD.template_id; END"])
SLOT=threading.BoundedSemaphore(1)


async def worker(request):
    from .parser_limits import ParserLimit
    while not SLOT.acquire(False):await asyncio.sleep(.05)
    process=None;limit=None
    try:
        command=[sys.executable,'-B','-m','RAG.drawing_worker']
        if sys.platform=='win32':
            bootstrap="import sys,runpy;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('RAG.drawing_worker',run_name='__main__')"
            command=[sys._base_executable,'-B','-c',bootstrap,str(Path(sys.prefix)/'Lib/site-packages')]
        process=await asyncio.create_subprocess_exec(*command,cwd=Path(__file__).resolve().parents[1],
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        limit=ParserLimit(process._transport.get_extra_info('subprocess'))
        payload=json.dumps(request).encode()
        if len(payload)>65536:raise ValueError('Drawing job input exceeds its budget.')
        output,error=await asyncio.wait_for(process.communicate(payload),30)
        if process.returncode or len(output)>8*1024*1024:raise ValueError('Drawing worker failed or exceeded resource limits.')
        result=json.loads(output)
        if 'error' in result:raise ValueError(result['error'])
        return result['result']
    finally:
        if process and process.returncode is None:process.kill();await process.wait()
        if limit:limit.close()
        SLOT.release()


class Drawings:
    def __init__(self,docs):self.docs=docs;self.tasks={}

    def file(self,cid,fid):
        row=self.docs.row(cid,fid)
        if not row:raise SourceInvalidated()
        if row['mime_type']!='application/pdf':raise ValueError('Drawing analysis currently accepts PDF pages.')
        return row

    def revision(self,cid,fid):
        self.file(cid,fid)
        with closing(self.docs.connect()) as db:
            row=db.execute('SELECT revision FROM drawing_state WHERE file_id=?',(fid,)).fetchone()
            return row[0] if row else 0

    def check(self,cid,versions):
        for fid,version in versions.items():
            if self.revision(cid,fid)!=version:raise SourceInvalidated()

    def load(self,cid,fid,payload):
        data=json.loads(payload)
        if 'artifact' not in data:return data
        self.file(cid,fid)
        with closing(self.docs.connect()) as db:
            row=db.execute('SELECT stored_filename,sha256 FROM drawing_artifacts WHERE id=? AND file_id=?',(data['artifact'],fid)).fetchone()
        if not row:raise SourceInvalidated()
        try:raw=self.docs.path(row['stored_filename']).read_bytes()
        except OSError as error:raise ValueError('Drawing artifact is unavailable; reanalysis is required.') from error
        if hashlib.sha256(raw).hexdigest()!=row['sha256']:raise ValueError('Drawing artifact failed its integrity check.')
        return json.loads(raw)

    def mutate(self,cid,fid,expected,operation,expected_revision=None,advance=True):
        with gate_for(self.docs,cid).hold(),closing(self.docs.connect()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT sha256 FROM files WHERE id=? AND conversation_id=?',(fid,cid)).fetchone()
            if not row or row[0]!=expected:raise SourceInvalidated()
            if expected_revision is not None:
                version=db.execute('SELECT revision FROM drawing_state WHERE file_id=?',(fid,)).fetchone()
                if (version[0] if version else 0)!=expected_revision:raise SourceInvalidated()
            operation(db)
            if advance:db.execute('INSERT INTO drawing_state VALUES(?,1) ON CONFLICT(file_id) DO UPDATE SET revision=revision+1',(fid,))
            version=db.execute('SELECT revision FROM drawing_state WHERE file_id=?',(fid,)).fetchone()
            return version[0] if version else 0

    async def analyze(self,cid,fid,page=1):
        file=self.file(cid,fid)
        with closing(self.docs.connect()) as db:
            old=db.execute("SELECT data FROM drawing_runs WHERE file_id=? AND source_hash=? AND page=? AND version=? AND status='ready' ORDER BY rowid DESC LIMIT 1",
                (fid,file['sha256'],page,VERSION)).fetchone()
            if old:return self.load(cid,fid,old[0])
        run_id='DRAW_'+uuid4().hex
        def start(db):db.execute('INSERT INTO drawing_runs VALUES(?,?,?,?,?,?,?)',(run_id,fid,file['sha256'],page,VERSION,'pending','{}'))
        self.mutate(cid,fid,file['sha256'],start)
        path=None;published=False
        try:
            result=await worker({'operation':'extract','path':str(self.docs.path(file['stored_filename'])),'page':page})
            result.update(id=run_id,file_id=fid,source_hash=file['sha256'],filename=file['original_filename'])
            result['validation']=coordinates(result,[f for f in result['fields'] if f['value'] is not None])
            raw=json.dumps(result,separators=(',',':'),allow_nan=False).encode()
            digest=hashlib.sha256(raw).hexdigest();artifact_id='ARTIFACT_'+uuid4().hex
            key=uuid4().hex+'.drawing';path=self.docs.path(key)
            temporary=path.with_suffix('.tmpdrawing')
            try:
                temporary.write_bytes(raw);os.replace(temporary,path)
            finally:temporary.unlink(missing_ok=True)
            def publish(db):
                db.execute('INSERT INTO drawing_artifacts VALUES(?,?,?,?)',(artifact_id,fid,key,digest))
                db.execute("UPDATE drawing_runs SET status='ready',data=? WHERE id=?",(json.dumps({'artifact':artifact_id,'sha256':digest}),run_id))
                db.execute('INSERT INTO drawing_validations VALUES(?,?,?)',('VALIDATION_'+uuid4().hex,fid,json.dumps({'analysis_id':run_id,'source_hash':file['sha256'],**result['validation']})))
            self.mutate(cid,fid,file['sha256'],publish)
            published=True
            return result
        except BaseException as error:
            if path is not None and not published:
                try:path.unlink(missing_ok=True)
                except OSError:
                    with closing(self.docs.connect()) as db,db:
                        db.execute('INSERT OR IGNORE INTO pending_unlinks VALUES(?)',(path.name,))
            try:
                self.mutate(cid,fid,file['sha256'],lambda db:db.execute("UPDATE drawing_runs SET status='failed',data=? WHERE id=?",
                    (json.dumps({'error':str(error) or 'Cancelled; retry is available.'}),run_id)))
            except SourceInvalidated:pass
            raise

    def get(self,cid,fid,run_id):
        file=self.file(cid,fid)
        with closing(self.docs.connect()) as db:
            row=db.execute('SELECT * FROM drawing_runs WHERE id=? AND file_id=? AND source_hash=?',(run_id,fid,file['sha256'])).fetchone()
        if not row:raise SourceInvalidated()
        return {'id':run_id,'status':row['status'],'data':self.load(cid,fid,row['data'])}

    async def render(self,cid,fid,page,bounds=None,scale=None):
        file=self.file(cid,fid);analysis=await self.analyze(cid,fid,page)
        g=analysis['geometry'];bounds=bounds or [0,0,g['width'],g['height']]
        if not valid_box(bounds,g['width'],g['height']):raise ValueError('Region outside this page.')
        scale=scale or min(1,2048/max(bounds[2]-bounds[0],bounds[3]-bounds[1]))
        result=await worker({'operation':'render','path':str(self.docs.path(file['stored_filename'])),'page':page,'bbox':bounds,'scale':scale})
        if self.file(cid,fid)['sha256']!=file['sha256']:raise SourceInvalidated()
        return base64.b64decode(result['png'])

    def templates(self,cid,fid):
        file=self.file(cid,fid);result=builtin_templates()
        with closing(self.docs.connect()) as db:
            for row in db.execute('SELECT id,data FROM drawing_templates WHERE file_id=? AND source_hash=?',(fid,file['sha256'])):
                result[row['id']]=json.loads(row['data'])
        return result

    async def add_template(self,cid,fid,body):
        file=self.file(cid,fid)
        if file['sha256']!=body.source_hash or self.revision(cid,fid)!=body.expected_revision:raise SourceInvalidated()
        analysis=await self.analyze(cid,fid,body.page)
        bounds=body.bbox;g=analysis['geometry']
        if not valid_box(bounds,g['width'],g['height']):raise ValueError('Invalid reference region.')
        png=await self.render(cid,fid,body.page,bounds,min(2,256/max(bounds[2]-bounds[0],bounds[3]-bounds[1])))
        tid='TEMPLATE_'+uuid4().hex
        data={'name':body.name,'version':1,'kind':'reference','png':base64.b64encode(png).decode(),
            'page':body.page,'bbox':bounds,'source_hash':file['sha256'],
            'limitations':['User-named reference; engineering class requires independent review.']}
        enclosed=[p for p in analysis['paths'] if p['bbox']==bounds]
        if len(enclosed)==1:data['points']=enclosed[0]['points']
        revision=self.mutate(cid,fid,file['sha256'],lambda db:db.execute('INSERT INTO drawing_templates VALUES(?,?,?,?)',(tid,fid,file['sha256'],json.dumps(data))),body.expected_revision)
        return {'id':tid,**data,'revision':revision}

    async def add_image_template(self,cid,fid,body):
        file=self.file(cid,fid)
        reference=self.docs.row(cid,body.reference_file_id)
        if not reference or not reference['mime_type'].startswith('image/'):raise ValueError('Select an available reference image in this conversation.')
        if file['sha256']!=body.source_hash or self.revision(cid,fid)!=body.expected_revision:raise SourceInvalidated()
        result=await worker({'operation':'reference_image','path':str(self.docs.path(reference['stored_filename'])),'page':1,'bbox':body.bbox})
        tid='TEMPLATE_'+uuid4().hex
        data={'name':body.name,'version':1,'kind':'reference','png':result['png'],
            'reference_file_id':reference['id'],'reference_hash':reference['sha256'],'bbox':body.bbox,
            'limitations':['Reference-symbol match only; the name is a user assertion, not an independently established engineering class.']}
        def publish(db):
            row=db.execute('SELECT sha256 FROM files WHERE id=? AND conversation_id=?',(reference['id'],cid)).fetchone()
            if not row or row[0]!=reference['sha256']:raise SourceInvalidated()
            db.execute('INSERT INTO drawing_templates VALUES(?,?,?,?)',(tid,fid,file['sha256'],json.dumps(data)))
            db.execute('INSERT INTO drawing_template_sources VALUES(?,?,?)',(tid,reference['id'],reference['sha256']))
        revision=self.mutate(cid,fid,file['sha256'],publish,body.expected_revision)
        return {'id':tid,**data,'revision':revision}

    async def count(self,cid,fid,pages,template_id,bounds=None):
        file=self.file(cid,fid);templates=self.templates(cid,fid)
        if template_id not in templates:raise ValueError('Select or define a supported symbol reference; text mentions cannot establish a visual count.')
        template=templates[template_id];findings=[];complete=True;processed=[];deadline=time.monotonic()+120
        scope={'file_id':fid,'source_hash':file['sha256'],'pages':sorted(set(pages)),'bbox':bounds,'template_id':template_id,'template_version':template['version']}
        for number in scope['pages']:
            analysis=await self.analyze(cid,fid,number);g=analysis['geometry']
            roi=bounds or [0,0,g['width'],g['height']]
            if not valid_box(roi,g['width'],g['height']):raise ValueError('Count region outside page.')
            findings.extend(f for f in match_vectors(analysis,template) if contains(roi,f['bbox']))
            if template.get('vector_profile') and analysis['coverage']['vectors_complete'] and not analysis['images']:
                if not analysis['coverage'].get('clipping_verified'):complete=False
                processed.append({'page':number,'bbox':roi,'method':'native_vector_profile'})
                continue
            scale=2
            for tile in tiles((roi[2]-roi[0])*scale,(roi[3]-roi[1])*scale):
                if time.monotonic()>deadline:complete=False;break
                region=[roi[0]+tile[0]/scale,roi[1]+tile[1]/scale,roi[0]+tile[2]/scale,roi[1]+tile[3]/scale]
                result=await worker({'operation':'detect','path':str(self.docs.path(file['stored_filename'])),'page':number,
                    'bbox':region,'scale':scale,'template':template})
                findings.extend(result['findings']);complete=complete and result['complete'];processed.append({'page':number,'bbox':region})
            if not analysis['coverage']['vectors_complete'] or not analysis['coverage'].get('clipping_verified'):complete=False
        findings=deduplicate(findings);review=None;analysis_revision=self.revision(cid,fid)
        if self.templates(cid,fid).get(template_id)!=template:raise SourceInvalidated()
        with closing(self.docs.connect()) as db:
            for row in db.execute('SELECT data FROM drawing_reviews WHERE file_id=? AND source_hash=? AND published=1 ORDER BY rowid DESC',(fid,file['sha256'])):
                candidate=json.loads(row[0])
                if candidate['scope']==scope:review=candidate;break
        label=count_label(scope,{'complete':complete,'classification_supported':True,'analysis_revision':analysis_revision},findings,review)
        total=len(review['objects']) if label=='Exact' else len(findings)
        verified_findings=[{**o,'method':'human_review','verification_id':review['verification_id'],'template_version':template['version'],'status':'human_reviewed'} for o in review['objects']] if label=='Exact' else findings
        result={'scope':scope,'label':label,'count':total,'detections':verified_findings,'automatic_count':len(findings),'processed_regions':processed,
            'coverage_complete':complete,'verification_id':review['verification_id'] if label=='Exact' else None,
            'limitations':['Automatic matches are not an independently verified total.',*template.get('limitations',[])]}
        if self.file(cid,fid)['sha256']!=file['sha256'] or self.revision(cid,fid)!=analysis_revision:raise SourceInvalidated()
        result['analysis_revision']=analysis_revision;result['id']='COUNT_'+uuid4().hex
        payload=json.dumps(result)
        self.mutate(cid,fid,file['sha256'],lambda db:db.execute('INSERT INTO drawing_count_runs VALUES(?,?,?,?)',(result['id'],fid,file['sha256'],payload)),analysis_revision,advance=False)
        return result

    def publish_review(self,cid,fid,body):
        file=self.file(cid,fid)
        if body.source_hash!=file['sha256'] or body.expected_revision!=self.revision(cid,fid):raise SourceInvalidated()
        if not body.independent or not body.exhaustive or body.unresolved:raise ValueError('Publication requires independent exhaustive review with no unresolved findings.')
        if set(body.reviewed_cells)!={f'{p}:{x}:{y}' for p in body.scope['pages'] for x in range(4) for y in range(4)}:
            raise ValueError('Every review grid cell must be checked against the original drawing.')
        if body.scope.get('file_id')!=fid or body.scope.get('source_hash')!=file['sha256']:raise SourceInvalidated()
        template=self.templates(cid,fid).get(body.scope['template_id'])
        if not template or template['version']!=body.scope['template_version']:raise ValueError('Review template is missing or changed.')
        for number in body.scope['pages']:
            with closing(self.docs.connect()) as db:
                row=db.execute("SELECT data FROM drawing_runs WHERE file_id=? AND page=? AND source_hash=? AND version=? AND status='ready' ORDER BY rowid DESC LIMIT 1",(fid,number,file['sha256'],VERSION)).fetchone()
            if not row:raise ValueError('Every reviewed page requires current original-page analysis, including zero-count pages.')
            geometry=self.load(cid,fid,row[0])['geometry']
            if body.scope['bbox'] is not None and not valid_box(body.scope['bbox'],geometry['width'],geometry['height']):raise ValueError('Review region outside original page.')
        if len({o['id'] for o in body.objects})!=len(body.objects):raise ValueError('Duplicate annotation IDs.')
        for obj in body.objects:
            if obj['class']!=template['name']:raise ValueError('Annotation class differs from the counting scope.')
            if obj['page'] not in body.scope['pages']:raise ValueError('Annotation outside reviewed pages.')
            with closing(self.docs.connect()) as db:
                row=db.execute("SELECT data FROM drawing_runs WHERE file_id=? AND page=? AND source_hash=? AND status='ready' ORDER BY rowid DESC LIMIT 1",(fid,obj['page'],file['sha256'])).fetchone()
            if not row:raise ValueError('Review requires original page analysis.')
            g=self.load(cid,fid,row[0])['geometry']
            if not valid_box(obj['bbox'],g['width'],g['height']):raise ValueError('Annotation outside PDF coordinates.')
            if body.scope['bbox'] is not None and not contains(body.scope['bbox'],obj['bbox']):raise ValueError('Annotation outside the reviewed region.')
        for i,obj in enumerate(body.objects):
            if any(obj['page']==other['page'] and iou(obj['bbox'],other['bbox'])>.9 for other in body.objects[i+1:]):
                raise ValueError('Overlapping duplicate annotations must be resolved before publication.')
        vid='VERIFY_'+uuid4().hex
        data={**body.model_dump(),'published':True,'verification_id':vid,'origin':'human_review_attestation',
              'verification_revision':body.expected_revision+1,'created_at':time.time(),'reviewer_identity':'self-declared local reviewer'}
        def save(db):
            revision=db.execute('SELECT revision FROM drawing_state WHERE file_id=?',(fid,)).fetchone()
            if (revision[0] if revision else 0)!=body.expected_revision:raise SourceInvalidated()
            db.execute('INSERT INTO drawing_reviews VALUES(?,?,?,?,?)',(vid,fid,file['sha256'],1,json.dumps(data)))
        revision=self.mutate(cid,fid,file['sha256'],save)
        return {**data,'revision':revision}
