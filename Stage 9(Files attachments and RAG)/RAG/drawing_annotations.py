"""Versioned human corrections; never promote automatic predictions to ground truth."""
from collections import deque
from contextlib import closing
import json
import time
from uuid import uuid4
from .drawing_geometry import valid_box
from .drawing_validation import convert
from .evidence_release import SourceInvalidated

SCHEMA="CREATE TABLE IF NOT EXISTS drawing_annotation_sets(id TEXT PRIMARY KEY,file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,source_hash TEXT NOT NULL,published INTEGER NOT NULL,data TEXT NOT NULL)"


def latest(service,cid,fid,published=True):
    file=service.file(cid,fid)
    with closing(service.docs.connect()) as db:
        row=db.execute('SELECT data FROM drawing_annotation_sets WHERE file_id=? AND source_hash=? AND published=? ORDER BY rowid DESC LIMIT 1',(fid,file['sha256'],int(published))).fetchone()
    return json.loads(row[0]) if row else None


async def save(service,cid,fid,body):
    file=service.file(cid,fid)
    if body.source_hash!=file['sha256'] or body.expected_revision!=service.revision(cid,fid):raise SourceInvalidated()
    if body.publish and (not body.attestation or not body.reviewer.strip()):raise ValueError('Publishing corrections requires an explicit human source-review attestation.')
    objects=[o.model_dump() for o in body.objects];ids={o['id'] for o in objects}
    if len(ids)!=len(objects):raise ValueError('Duplicate annotation IDs.')
    pages={}
    for number in {o['page'] for o in objects}:
        pages[number]=await service.analyze(cid,fid,number)
    for obj in objects:
        page=pages[obj['page']];g=page['geometry']
        if not valid_box(obj['bbox'],g['width'],g['height']):raise ValueError('Annotation outside its original PDF page.')
        evidence={p['id'] for p in page['paths']}|{w['id'] for w in page['words']}
        if not obj['source_ids'] or not set(obj['source_ids']).issubset(evidence):raise ValueError('Annotations require existing native source references on the same page.')
        if obj['kind']=='dimension':
            if not obj['object_id'] or obj['value'] is None or obj['unit'] is None:raise ValueError('Dimensions require an identified object, value and supported unit.')
            convert(obj['value'],obj['unit'],obj['unit'])
        if obj['kind']=='connection':
            endpoints=[next((o for o in objects if o['id']==key),None) for key in (obj['start'],obj['end'])]
            if any(o is None or o['kind'] not in ('symbol','junction') or o['page']!=obj['page'] for o in endpoints):raise ValueError('Connections require two annotated objects on the same page; missing sheets remain unresolved.')
            if obj['start']==obj['end']:raise ValueError('Connection endpoints must differ.')
    record={'id':'ANNOTATION_'+uuid4().hex,'source_hash':file['sha256'],'objects':objects,'reviewer':body.reviewer,
        'published':body.publish,'attestation':body.attestation,'origin':'human_review_attestation' if body.publish else 'manual_draft',
        'reviewer_identity':'self-declared local reviewer','created_at':time.time(),'scope':'listed objects only; not exhaustive count verification'}
    revision=service.mutate(cid,fid,file['sha256'],lambda db:db.execute('INSERT INTO drawing_annotation_sets VALUES(?,?,?,?,?)',
        (record['id'],fid,file['sha256'],int(body.publish),json.dumps(record))),body.expected_revision)
    return {**record,'revision':revision}


def trace_reviewed(record,question):
    if not record or not record.get('published'):return None
    objects=record['objects'];nodes={o['id']:o for o in objects if o['kind'] in ('symbol','junction')}
    named=[o for o in nodes.values() if o['label'] and o['label'].casefold() in question.casefold()]
    if len(named)!=2:return {'status':'ambiguous_objects','candidates':[o['label'] for o in named]}
    named.sort(key=lambda o:question.casefold().index(o['label'].casefold()))
    adjacency={key:[] for key in nodes}
    for edge in objects:
        if edge['kind']!='connection' or edge['line_type']!='process':continue
        adjacency[edge['start']].append((edge['end'],edge))
        if edge['direction']!='start_to_end':adjacency[edge['end']].append((edge['start'],edge))
    queue=deque([(named[0]['id'],[])]);seen={named[0]['id']}
    while queue:
        node,path=queue.popleft()
        if node==named[1]['id']:
            return {'status':'human_reviewed_path','start':named[0]['label'],'end':named[1]['label'],
                'edges':path,'verification_id':record['id'],'reviewer':record['reviewer'],
                'direction':'supported' if all(e['direction']=='start_to_end' for e in path) else 'undirected_or_partial',
                'limitation':'Human-attested listed connections; off-page and unreviewed paths are not covered.'}
        for neighbor,edge in adjacency[node]:
            if neighbor not in seen:seen.add(neighbor);queue.append((neighbor,path+[edge]))
    return {'status':'unresolved','start':named[0]['label'],'end':named[1]['label'],'edges':[],
        'limitation':'No reviewed process path; missing sheets, crossings, and unreviewed connections remain unresolved.'}
