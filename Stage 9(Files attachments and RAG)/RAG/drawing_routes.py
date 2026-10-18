"""Conversation-scoped drawing APIs; review actions are explicit human attestations."""
import asyncio
import json
import math
from contextlib import closing
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Query, Depends
from fastapi.responses import Response
from fastapi.routing import APIRoute
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,field_validator
from .drawings import Drawings
from .drawing_graph import trace
from .drawing_validation import evaluate
from .evidence_release import SourceInvalidated,release_gate,SEND_TIMEOUT


class Input(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,allow_inf_nan=False)


class PageInput(Input):
    page:int=Field(default=1,ge=1,le=200)


class TemplateInput(PageInput):
    source_hash:str=Field(min_length=64,max_length=64)
    expected_revision:int=Field(ge=0)
    name:str=Field(min_length=1,max_length=80)
    bbox:list[float]=Field(min_length=4,max_length=4)


class ReferenceImageInput(Input):
    source_hash:str=Field(min_length=64,max_length=64)
    expected_revision:int=Field(ge=0)
    reference_file_id:str=Field(min_length=1,max_length=80)
    name:str=Field(min_length=1,max_length=80)
    bbox:list[float]|None=Field(default=None,min_length=4,max_length=4)


class Annotation(Input):
    id:str=Field(min_length=1,max_length=100)
    kind:Literal['symbol','dimension','junction','connection','gap']
    page:int=Field(ge=1,le=200)
    bbox:list[float]=Field(min_length=4,max_length=4)
    label:str=Field(min_length=1,max_length=100)
    source_ids:list[str]=Field(min_length=1,max_length=200)
    object_id:str|None=Field(default=None,max_length=100)
    value:str|None=Field(default=None,max_length=40,pattern=r'^[+-]?\d{1,20}(?:\.\d{1,12})?$')
    unit:Literal['mm','cm','m','in','ft']|None=None
    start:str|None=Field(default=None,max_length=100)
    end:str|None=Field(default=None,max_length=100)
    line_type:Literal['process','signal','dimension','annotation','unknown']='unknown'
    direction:Literal['start_to_end','undirected','unknown']='unknown'


class AnnotationSet(Input):
    source_hash:str=Field(min_length=64,max_length=64)
    expected_revision:int=Field(ge=0)
    reviewer:str=Field(min_length=2,max_length=100)
    objects:list[Annotation]=Field(max_length=1000)
    publish:bool=False
    attestation:bool=False


class CountInput(Input):
    pages:list[int]=Field(min_length=1,max_length=200)
    template_id:str=Field(min_length=1,max_length=80)
    bbox:list[float]|None=Field(default=None,min_length=4,max_length=4)
    @field_validator('pages')
    @classmethod
    def valid_pages(cls,value):
        if len(value)!=len(set(value)) or any(not 1<=p<=200 for p in value):raise ValueError('Invalid page scope.')
        return value


class ReviewInput(Input):
    source_hash:str=Field(min_length=64,max_length=64)
    expected_revision:int=Field(ge=0)
    reviewer:str=Field(min_length=2,max_length=100)
    scope:dict
    objects:list[dict]=Field(max_length=2000)
    reviewed_cells:list[str]=Field(default_factory=list,max_length=3200)
    independent:bool=False
    exhaustive:bool=False
    unresolved:list[str]=Field(default_factory=list,max_length=100)
    @field_validator('unresolved')
    @classmethod
    def unresolved_valid(cls,items):
        if any(not item.strip() or len(item)>500 for item in items):raise ValueError('Unresolved items require 1 to 500 characters each.')
        return [item.strip() for item in items]
    @field_validator('scope')
    @classmethod
    def scope_valid(cls,value):
        if set(value)!={'file_id','source_hash','pages','bbox','template_id','template_version'}:raise ValueError('Review requires the complete count scope.')
        CountInput.model_validate({k:value[k] for k in ('pages','bbox','template_id')})
        if type(value['template_version']) is not int or value['template_version']<1:raise ValueError('Invalid template version.')
        return value
    @field_validator('objects')
    @classmethod
    def objects_valid(cls,objects):
        for obj in objects:
            if set(obj)!={'id','page','bbox','class'}:raise ValueError('Annotations require id, page, bbox and class.')
            if not isinstance(obj['id'],str) or not 1<=len(obj['id'])<=100 or not isinstance(obj['class'],str) or not 1<=len(obj['class'])<=80:raise ValueError('Invalid annotation identity/class.')
            if type(obj['page']) is not int or not 1<=obj['page']<=200:raise ValueError('Invalid annotation page.')
            if not isinstance(obj['bbox'],list) or len(obj['bbox'])!=4 or any(type(v) not in (int,float) or not math.isfinite(v) for v in obj['bbox']):raise ValueError('Invalid annotation coordinates.')
        return objects


class TraceInput(PageInput):
    start:str=Field(min_length=1,max_length=80)
    end:str=Field(min_length=1,max_length=80)


def router_for(docs):
    service=Drawings(docs)
    def authorized(cid:int,fid:str):
        row=docs.row(cid,fid)
        if row is None:raise HTTPException(404,'Drawing is unavailable in this conversation.')
        if row['mime_type']!='application/pdf':raise HTTPException(422,'Select a PDF drawing.')
    class GuardedImage(Response):
        async def __call__(self,scope,receive,send):
            async with release_gate(docs,self.cid):
                try:
                    if service.file(self.cid,self.fid)['sha256']!=self.source_hash:raise SourceInvalidated()
                    service.check(self.cid,{self.fid:self.revision})
                except SourceInvalidated:
                    await Response('Source changed; reload the drawing.',status_code=409)(scope,receive,send)
                    return
                await asyncio.wait_for(super().__call__(scope,receive,send),SEND_TIMEOUT)
    class GuardedResult(Response):
        async def __call__(self,scope,receive,send):
            async with release_gate(docs,self.cid):
                try:
                    if service.file(self.cid,self.fid)['sha256']!=self.source_hash:raise SourceInvalidated()
                    service.check(self.cid,{self.fid:self.revision})
                except SourceInvalidated:
                    await Response('Source or analysis changed; reload the drawing.',status_code=409)(scope,receive,send)
                    return
                await asyncio.wait_for(super().__call__(scope,receive,send),SEND_TIMEOUT)
    class ScopedRoute(APIRoute):
        def get_route_handler(self):
            handler=super().get_route_handler()
            async def guarded(request):
                try:cid=int(request.path_params['cid']);fid=request.path_params['fid']
                except (KeyError,ValueError):return await handler(request)
                before=docs.row(cid,fid)
                try:before_revision=service.revision(cid,fid) if before and before['mime_type']=='application/pdf' else None
                except SourceInvalidated:return Response('Source changed.',status_code=409)
                original=await handler(request)
                if original.status_code>=400 or isinstance(original,GuardedImage):return original
                if not before:return Response('Source unavailable.',status_code=409)
                data=json.loads(original.body)
                revision=data.get('analysis_revision',data.get('revision')) if isinstance(data,dict) else None
                if type(revision) is not int:
                    revision=before_revision
                response=GuardedResult(original.body,status_code=original.status_code,headers=dict(original.headers),media_type=original.media_type,background=original.background)
                response.cid=cid;response.fid=fid;response.source_hash=before['sha256'];response.revision=revision
                return response
            return guarded
    router=APIRouter(prefix='/conversations/{cid}/files/{fid}/drawing',dependencies=[Depends(authorized)],route_class=ScopedRoute)
    async def checked(call):
        try:return await call
        except SourceInvalidated as error:raise HTTPException(409,str(error)) from error
        except (ValueError,KeyError) as error:raise HTTPException(422,str(error)) from error
    @router.post('/analyze')
    async def analyze(cid:int,fid:str,body:PageInput):
        result=await checked(service.analyze(cid,fid,body.page))
        return {'id':result['id'],'status':'ready','revision':service.revision(cid,fid),'data':result}
    @router.get('/runs/{run_id}')
    def run(cid:int,fid:str,run_id:str):
        try:return service.get(cid,fid,run_id)
        except SourceInvalidated as error:raise HTTPException(404,str(error)) from error
    @router.get('/render')
    async def render(cid:int,fid:str,page:int=Query(1,ge=1,le=200),bbox:str|None=None,scale:float|None=None):
        try:bounds=json.loads(bbox) if bbox else None
        except ValueError as error:raise HTTPException(422,'Invalid region.') from error
        before=service.file(cid,fid)['sha256']
        png=await checked(service.render(cid,fid,page,bounds,scale))
        # Recheck under the same mutation gate immediately before constructing the response.
        async with release_gate(docs,cid):
            if service.file(cid,fid)['sha256']!=before:raise HTTPException(409,'Source changed.')
            response=GuardedImage(png,media_type='image/png',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
            response.cid=cid;response.fid=fid;response.source_hash=before;response.revision=service.revision(cid,fid)
            return response
    @router.get('/templates')
    def templates(cid:int,fid:str):return {'templates':service.templates(cid,fid)}
    @router.post('/templates')
    async def template(cid:int,fid:str,body:TemplateInput):return await checked(service.add_template(cid,fid,body))
    @router.post('/templates/image')
    async def image_template(cid:int,fid:str,body:ReferenceImageInput):return await checked(service.add_image_template(cid,fid,body))
    @router.get('/annotations')
    def annotations(cid:int,fid:str):
        from .drawing_annotations import latest
        revision=service.revision(cid,fid)
        return {'published':latest(service,cid,fid),'draft':latest(service,cid,fid,False),'revision':revision}
    @router.post('/annotations')
    async def annotate(cid:int,fid:str,body:AnnotationSet):
        from .drawing_annotations import save
        return await checked(save(service,cid,fid,body))
    @router.post('/count')
    async def count(cid:int,fid:str,body:CountInput):return await checked(service.count(cid,fid,body.pages,body.template_id,body.bbox))
    @router.get('/counts/{count_id}')
    def count_result(cid:int,fid:str,count_id:str):
        file=service.file(cid,fid)
        with closing(docs.connect()) as db:
            row=db.execute('SELECT data FROM drawing_count_runs WHERE id=? AND file_id=? AND source_hash=?',(count_id,fid,file['sha256'])).fetchone()
        if not row:raise HTTPException(404,'Count evidence is unavailable.')
        result=json.loads(row[0])
        try:service.check(cid,{fid:result['analysis_revision']})
        except SourceInvalidated as error:raise HTTPException(409,'Count evidence changed; run the count again.') from error
        return result
    @router.post('/trace')
    async def trace_path(cid:int,fid:str,body:TraceInput):
        data=await checked(service.analyze(cid,fid,body.page))
        try:return trace(data['graph'],body.start,body.end)
        except ValueError as error:raise HTTPException(422,str(error)) from error
    @router.post('/reviews/draft')
    def draft(cid:int,fid:str,body:ReviewInput):
        file=service.file(cid,fid);rid='DRAFT_'+uuid4().hex
        if body.source_hash!=file['sha256'] or body.expected_revision!=service.revision(cid,fid):raise HTTPException(409,'Stale review.')
        data={**body.model_dump(),'published':False,'origin':'manual_draft','saved_revision':body.expected_revision+1}
        try:revision=service.mutate(cid,fid,file['sha256'],lambda db:db.execute('INSERT INTO drawing_reviews VALUES(?,?,?,?,?)',(rid,fid,file['sha256'],0,json.dumps(data))),body.expected_revision)
        except SourceInvalidated as error:raise HTTPException(409,str(error)) from error
        return {'id':rid,'revision':revision,'data':data}
    @router.post('/reviews/publish')
    def publish(cid:int,fid:str,body:ReviewInput):
        try:return service.publish_review(cid,fid,body)
        except SourceInvalidated as error:raise HTTPException(409,str(error)) from error
        except ValueError as error:raise HTTPException(422,str(error)) from error
    @router.get('/reviews')
    def reviews(cid:int,fid:str):
        file=service.file(cid,fid)
        with closing(docs.connect()) as db:
            return {'reviews':[{'id':r['id'],**json.loads(r['data'])} for r in db.execute('SELECT id,data FROM drawing_reviews WHERE file_id=? AND source_hash=? ORDER BY rowid DESC',(fid,file['sha256']))]}
    @router.get('/reviews/{review_id}')
    def restore_review(cid:int,fid:str,review_id:str):
        file=service.file(cid,fid);revision=service.revision(cid,fid)
        with closing(docs.connect()) as db:
            row=db.execute('SELECT data FROM drawing_reviews WHERE id=? AND file_id=? AND source_hash=?',(review_id,fid,file['sha256'])).fetchone()
        if not row:raise HTTPException(404,'Review draft is unavailable in this drawing.')
        data=json.loads(row[0]);scope=data.get('scope',{})
        template=service.templates(cid,fid).get(scope.get('template_id'))
        if (data.get('published') is not False or data.get('saved_revision')!=revision or
            scope.get('file_id')!=fid or scope.get('source_hash')!=file['sha256'] or
            not template or scope.get('template_version')!=template['version']):
            raise HTTPException(409,'Review draft is stale or its scope changed; inspect the original again.')
        # Restoring is read-only. Attestation must be made again at publication.
        return {'id':review_id,'revision':revision,'data':{**data,'independent':False,'exhaustive':False}}
    @router.get('/validations')
    def validations(cid:int,fid:str):
        file=service.file(cid,fid)
        with closing(docs.connect()) as db:
            reports=[{'id':r['id'],**json.loads(r['data'])} for r in db.execute('SELECT id,data FROM drawing_validations WHERE file_id=?',(fid,))]
        return {'reports':[r for r in reports if r['source_hash']==file['sha256']]}
    return router
