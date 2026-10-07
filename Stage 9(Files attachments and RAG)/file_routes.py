"""Upload/download APIs; every file/source lookup is scoped to a conversation."""
import asyncio
from contextlib import closing
import json
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from documents import MAX_FILE

class UploadBodyLimit:
    """Bound the multipart body before Starlette can spool an oversized upload."""
    def __init__(self, app):
        self.app = app
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("POST","PUT") or "/files" not in scope["path"]:
            return await self.app(scope,receive,send)
        length = dict(scope["headers"]).get(b"content-length")
        if length and int(length) > MAX_FILE + 65536:
            from starlette.responses import JSONResponse
            return await JSONResponse({"detail":"Upload exceeds 10 MiB."},status_code=413)(scope,receive,send)
        count = 0
        async def limited():
            nonlocal count
            event = await receive()
            count += len(event.get("body",b""))
            if count > MAX_FILE + 65536:
                raise HTTPException(413,"Upload exceeds 10 MiB.")
            return event
        await self.app(scope,limited,send)

def router_for(app_module):
    docs, rag = app_module.documents, app_module.rag
    router = APIRouter()

    def conversation(cid):
        if not app_module.conversation_exists(cid):
            raise HTTPException(404,"Conversation not found.")

    def file(cid,fid):
        row = docs.row(cid,fid)
        if row is None:
            raise HTTPException(404,"File not found in this conversation.")
        return row

    @router.get("/files/config")
    def config():
        return {"max_file_size":MAX_FILE,"max_files":4,"max_total_size":20*1024*1024,
                "debug":rag.debug,"embedding_model":rag.provider.embed_model,"vision_model":rag.provider.vision_model or None}

    @router.get("/conversations/{cid}/files")
    def files(cid:int):
        conversation(cid)
        return {"files":docs.list(cid)}

    @router.post("/conversations/{cid}/files",status_code=201)
    async def upload(cid:int, upload:UploadFile=File(...)):
        conversation(cid)
        try:
            data = await upload.read(MAX_FILE+1)
            result, created = await run_in_threadpool(docs.accept,cid,upload.filename,upload.content_type,data)
        except ValueError as error:
            raise HTTPException(422,str(error)) from error
        finally:
            await upload.close()
        if result["state"] == "uploaded" or (result["state"]=="preview" and rag.provider.vision_model):
            rag.schedule(cid,result["id"])
        return {"file":result,"duplicate":not created}

    @router.put("/conversations/{cid}/files/{fid}")
    async def replace(cid:int,fid:str,upload:UploadFile=File(...)):
        if not app_module.conversation_lock.acquire(cid):
            await upload.close()
            raise HTTPException(409,"Stop the response before replacing a file.")
        try:
            file(cid,fid)
            task = rag.tasks.get(fid)
            if task:
                task.cancel()
                await asyncio.gather(task,return_exceptions=True)
            data = await upload.read(MAX_FILE+1)
            result,created = await run_in_threadpool(docs.replace,cid,fid,upload.filename,upload.content_type,data)
            if result["state"] in ("uploaded","failed") or (result["state"]=="preview" and rag.provider.vision_model):
                rag.schedule(cid,result["id"])
            return {"file":result,"duplicate":not created}
        except ValueError as error:
            raise HTTPException(422,str(error)) from error
        finally:
            await upload.close()
            app_module.conversation_lock.release(cid)

    @router.post("/conversations/{cid}/files/{fid}/reindex",status_code=202)
    async def reindex(cid:int,fid:str):
        row = file(cid,fid)
        if app_module.conversation_lock.locked(cid):
            raise HTTPException(409,"Wait for the response to finish before re-indexing.")
        if row["mime_type"].startswith("image/") and not rag.provider.vision_model:
            raise HTTPException(422,"Configure VISION_MODEL to index images. Preview/download remain available.")
        return {"scheduled":rag.schedule(cid,fid)}

    @router.delete("/conversations/{cid}/files/{fid}")
    async def delete(cid:int,fid:str):
        if not app_module.conversation_lock.acquire(cid):
            raise HTTPException(409,"Stop the response before deleting a source file.")
        try:
            file(cid,fid)
            task = rag.tasks.get(fid)
            if task:
                task.cancel()
                await asyncio.gather(task,return_exceptions=True)
            with closing(docs.connect()) as db, db:
                db.execute("DELETE FROM files WHERE id=? AND conversation_id=?",(fid,cid))
                # Stored debug contexts can contain deleted document text; erase them too.
                db.execute("DELETE FROM rag_runs WHERE message_id IN (SELECT id FROM messages WHERE conversation_id=?)",(cid,))
            await run_in_threadpool(docs.cleanup)
        finally:
            app_module.conversation_lock.release(cid)
        return {"deleted":fid}

    @router.get("/conversations/{cid}/files/{fid}/download")
    def download(cid:int,fid:str,preview:bool=False):
        row = file(cid,fid)
        path = docs.path(row["stored_filename"])
        if not path.is_file():
            raise HTTPException(410,"The stored file is unavailable. Upload it again.")
        inline = preview and row["mime_type"].startswith("image/")
        return FileResponse(path,media_type=row["mime_type"],filename=row["original_filename"],
            content_disposition_type="inline" if inline else "attachment",
            headers={"X-Content-Type-Options":"nosniff","Content-Security-Policy":"sandbox; default-src 'none'",
                     "Cache-Control":"no-store"})

    @router.get("/conversations/{cid}/files/{fid}/preview")
    def preview(cid:int,fid:str,offset:int=0):
        row = file(cid,fid)
        sections = json.loads(row["extracted"] or "[]")
        start = max(0,offset)
        return {"file":docs.public(row),"sections":sections[start:start+10],
                "next":start+10 if start+10<len(sections) else None}

    @router.get("/conversations/{cid}/sources/{source_id}")
    def source(cid:int,source_id:str):
        with closing(docs.connect()) as db:
            row = db.execute("""SELECT c.id,c.text,c.metadata,f.id file_id,f.original_filename FROM chunks c
                JOIN files f ON f.id=c.file_id WHERE c.id=? AND f.conversation_id=?""",(source_id,cid)).fetchone()
        if not row and source_id.startswith("SOURCE_"):
            with closing(docs.connect()) as db:
                block=db.execute("""SELECT b.id,b.data,b.file_id,f.original_filename FROM document_blocks b
                    JOIN files f ON f.id=b.file_id WHERE b.id=? AND f.conversation_id=?""",
                    (source_id.replace("SOURCE_","BLOCK_"),cid)).fetchone()
            if block:
                data=json.loads(block["data"])
                return {"id":source_id,"file_id":block["file_id"],"original_filename":block["original_filename"],
                    "text":data["text"],"metadata":{k:v for k,v in data.items() if k!="text"}}
        if not row:
            raise HTTPException(404,"Source is no longer available; it may have been deleted or updated.")
        return dict(row) | {"metadata":json.loads(row["metadata"])}

    @router.get("/conversations/{cid}/messages/{mid}/retrieval")
    def debug(cid:int,mid:int):
        if not rag.debug:
            raise HTTPException(404,"Developer retrieval diagnostics are disabled.")
        with closing(docs.connect()) as db:
            row = db.execute("""SELECT r.details FROM rag_runs r JOIN messages m ON m.id=r.message_id
                WHERE m.id=? AND m.conversation_id=?""",(mid,cid)).fetchone()
        if not row:
            raise HTTPException(404,"No retrieval diagnostics for this message.")
        return json.loads(row[0])
    @router.get("/conversations/{cid}/tool-evidence/{tool_id}")
    def tool_evidence(cid:int,tool_id:str):
        with closing(docs.connect()) as db:
            row=db.execute("SELECT data FROM tool_runs WHERE id=? AND conversation_id=?",(tool_id,cid)).fetchone()
        if not row:
            raise HTTPException(404,"Tool evidence is unavailable; its source may have been deleted or changed.")
        return json.loads(row[0])
    return router
