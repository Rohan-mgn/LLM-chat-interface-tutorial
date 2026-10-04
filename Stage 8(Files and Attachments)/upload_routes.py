"""Bounded multipart endpoints. Downloads never expose a filesystem path."""
import asyncio
from uuid import UUID

from fastapi import File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from starlette.formparsers import MultiPartParser
from starlette.concurrency import run_in_threadpool

from attachments import MAX_FILE_SIZE, MAX_TOTAL_SIZE, MAX_FILES, FORMATS
from tree_store import TreeError

MAX_REQUEST_SIZE = MAX_FILE_SIZE + 64 * 1024


class UploadBodyLimit:
    """Bound the complete multipart envelope BEFORE FastAPI parses/spools it.

    Check actual ASGI bytes as well as Content-Length (which can be absent or
    dishonest). A single bounded buffer also avoids partial parser temp files.
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] != 'POST' or '/files/' not in scope['path']:
            return await self.app(scope, receive, send)
        async def reject():
            await JSONResponse({'detail': 'Upload exceeds the 10 MiB file limit.'}, status_code=413)(scope, receive, send)
        headers = dict(scope.get('headers', []))
        try:
            if int(headers.get(b'content-length', b'0')) > MAX_REQUEST_SIZE:
                return await reject()
        except ValueError:
            return await reject()
        chunks, size = [], 0
        while True:
            try:
                event = await asyncio.wait_for(receive(), timeout=60)
            except TimeoutError:
                return await JSONResponse({'detail': 'Upload timed out.'}, status_code=408)(scope, receive, send)
            if event['type'] == 'http.disconnect':
                return
            size += len(event.get('body', b''))
            if size > MAX_REQUEST_SIZE:
                return await reject()
            chunks.append(event.get('body', b''))
            if not event.get('more_body', False):
                break
        body = b''.join(chunks)
        delivered = False
        async def bounded_receive():
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {'type': 'http.request', 'body': body, 'more_body': False}
        await self.app(scope, bounded_receive, send)


def register(app, files, conversation_exists):
    app.add_middleware(UploadBodyLimit)
    # The middleware caps total bytes first. Keep this bounded multipart spool
    # in memory; all durable file bytes go directly to Stage 8's directory on D:.
    MultiPartParser.spool_max_size = MAX_REQUEST_SIZE

    @app.exception_handler(TreeError)
    async def attachment_error(request, error):
        return JSONResponse({'detail': str(error)}, status_code=error.status)

    @app.get('/attachment-limits')
    def limits():
        return {'max_file_size': MAX_FILE_SIZE, 'max_total_size': MAX_TOTAL_SIZE,
                'max_files': MAX_FILES, 'extensions': list(FORMATS)}

    @app.post('/conversations/{conversation_id}/files/{file_id}', status_code=201)
    async def upload(conversation_id: int, file_id: UUID, file: UploadFile = File(...)):
        try:
            if not conversation_exists(conversation_id):
                raise HTTPException(404, 'Conversation not found.')
            return await files.upload(conversation_id, str(file_id), file)
        finally:
            await file.close()

    @app.get('/conversations/{conversation_id}/files')
    def pending(conversation_id: int):
        if not conversation_exists(conversation_id):
            raise HTTPException(404, 'Conversation not found.')
        return {'files': files.pending(conversation_id)}

    @app.delete('/conversations/{conversation_id}/files/{file_id}')
    async def remove(conversation_id: int, file_id: UUID):
        await run_in_threadpool(files.remove, conversation_id, str(file_id))
        return {'deleted': str(file_id)}

    @app.get('/conversations/{conversation_id}/files/{file_id}')
    def download(conversation_id: int, file_id: UUID, preview: bool = False):
        row = files.find(conversation_id, str(file_id))
        key = row['preview_key'] if preview else row['storage_key']
        if not key or not files.path(key).is_file():
            raise HTTPException(404, 'Attachment is unavailable on disk.')
        return FileResponse(files.path(key), media_type='image/png' if preview else row['mime_type'],
                            filename='preview.png' if preview else row['original_filename'],
                            content_disposition_type='inline' if preview else 'attachment',
                            headers={'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store',
                                     'Content-Security-Policy': "sandbox; default-src 'none'"})
