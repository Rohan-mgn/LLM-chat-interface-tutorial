"""Attachment storage and validation, independent of the HTTP/UI layer.

Files belong to one conversation. A join table lets edited message branches
share the same immutable blob without copying bytes or stealing ownership.
"""
from contextlib import closing
from pathlib import Path
import re
import unicodedata
import warnings
from uuid import uuid4

from PIL import Image, UnidentifiedImageError
from tree_store import TreeError

MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_TOTAL_SIZE = 25 * 1024 * 1024
MAX_FILES = 5
MAX_PIXELS = 20_000_000
FORMATS = {'.txt': 'text/plain', '.md': 'text/markdown', '.csv': 'text/csv',
           '.pdf': 'application/pdf', '.png': 'image/png', '.jpg': 'image/jpeg',
           '.jpeg': 'image/jpeg', '.webp': 'image/webp'}
IMAGE_FORMATS = {'.png': 'PNG', '.jpg': 'JPEG', '.jpeg': 'JPEG', '.webp': 'WEBP'}
STORAGE_KEY = re.compile(r'^[a-f0-9]{32}(?:\.blob|\.preview\.png|\.[a-f0-9]{32}\.part)$')


def migrate(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS files (
        id TEXT PRIMARY KEY,
        conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        original_filename TEXT NOT NULL, storage_key TEXT NOT NULL UNIQUE,
        mime_type TEXT NOT NULL, size INTEGER NOT NULL CHECK(size > 0),
        preview_key TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(id, conversation_id)
    )''')
    connection.execute('CREATE UNIQUE INDEX IF NOT EXISTS message_scope ON messages(id, conversation_id)')
    connection.execute('''CREATE TABLE IF NOT EXISTS message_files (
        message_id INTEGER NOT NULL, file_id TEXT NOT NULL, conversation_id INTEGER NOT NULL,
        PRIMARY KEY(message_id, file_id),
        FOREIGN KEY(message_id, conversation_id) REFERENCES messages(id, conversation_id) ON DELETE CASCADE,
        FOREIGN KEY(file_id, conversation_id) REFERENCES files(id, conversation_id) ON DELETE CASCADE
    )''')
    connection.execute('CREATE INDEX IF NOT EXISTS file_links ON message_files(file_id)')
    connection.execute('''CREATE TABLE IF NOT EXISTS cancelled_uploads (
        id TEXT PRIMARY KEY,
        conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE
    )''')
    connection.execute('CREATE TABLE IF NOT EXISTS file_deletions (storage_key TEXT PRIMARY KEY)')
    # The deletion intent commits together with the SQL delete. An OS error or
    # a crash cannot erase that intent; the next sweep retries physical cleanup.
    connection.execute('''CREATE TRIGGER IF NOT EXISTS queue_deleted_file AFTER DELETE ON files
        BEGIN
          INSERT OR IGNORE INTO file_deletions VALUES (OLD.storage_key);
          INSERT OR IGNORE INTO file_deletions SELECT OLD.preview_key WHERE OLD.preview_key IS NOT NULL;
        END''')
    connection.execute('''CREATE TRIGGER IF NOT EXISTS unlink_last_file AFTER DELETE ON message_files
        BEGIN DELETE FROM files WHERE id = OLD.file_id
          AND NOT EXISTS (SELECT 1 FROM message_files WHERE file_id = OLD.file_id); END''')


def safe_name(raw):
    name = unicodedata.normalize('NFC', (raw or '').replace('\\', '/').split('/')[-1])
    name = ''.join(char for char in name if not unicodedata.category(char).startswith('C'))
    name = re.sub(r'[<>:"|?*]', '_', name).strip(' .')
    if not name:
        raise TreeError(422, 'A filename is required.')
    suffix = Path(name).suffix.lower()
    if suffix not in FORMATS:
        raise TreeError(415, 'Supported files: TXT, MD, CSV, PDF, PNG, JPEG, and WebP. DOCX is not supported yet.')
    return name[:160 - len(suffix)] + suffix if len(name) > 160 else name


def validate_file(path, name, declared_type, preview_path):
    """Check bytes as well as extension/MIME. Never execute uploaded content."""
    suffix = Path(name).suffix.lower()
    mime = FORMATS[suffix]
    accepted = {mime, '', 'application/octet-stream'}
    if suffix in ('.txt', '.md', '.csv'):
        accepted.add('text/plain')
    if suffix == '.md':
        accepted.add('text/x-markdown')
    if suffix == '.csv':
        accepted.add('application/vnd.ms-excel')  # Common Windows CSV MIME hint.
    if (declared_type or '').split(';')[0].strip().lower() not in accepted:
        raise TreeError(415, 'The declared file type does not match its extension.')
    if suffix in IMAGE_FORMATS:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(path) as image:
                    if image.format != IMAGE_FORMATS[suffix] or image.width * image.height > MAX_PIXELS:
                        raise ValueError('Wrong image format or image exceeds 20 megapixels')
                    image.verify()
                with Image.open(path) as image:
                    image.load()
                    image.thumbnail((512, 512))
                    # Decode/re-encode the preview; never inline the original
                    # upload or pass through its metadata or embedded payloads.
                    clean = image.convert('RGBA')
                    clean.info.clear()
                    clean.save(preview_path, format='PNG')
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
            raise TreeError(415, 'Invalid image or image exceeds 20 megapixels.') from error
        return mime, True
    data = path.read_bytes()
    if suffix == '.pdf':
        if not data.startswith(b'%PDF-') or b'%%EOF' not in data[-2048:]:
            raise TreeError(415, 'The file does not have a valid PDF signature and ending.')
    else:
        try:
            text = data.decode('utf-8-sig')
        except UnicodeDecodeError as error:
            raise TreeError(415, 'Text, Markdown, and CSV files must use UTF-8 encoding.') from error
        if any(ord(char) < 32 and char not in '\t\r\n\f' for char in text):
            raise TreeError(415, 'Binary content is not allowed in text files.')
        if data.startswith((b'%PDF-', b'MZ', b'PK\x03\x04')):
            raise TreeError(415, 'The file content does not match a plain text format.')
    return mime, False


def bind_files(connection, conversation_id, message_id, file_ids, source_message_id=None):
    """Called inside the same transaction that inserts the new user branch."""
    if len(file_ids) != len(set(file_ids)) or len(file_ids) > MAX_FILES:
        raise TreeError(422, 'Choose at most 5 different attachments.')
    rows = []
    for file_id in file_ids:
        row = connection.execute('SELECT * FROM files WHERE id = ? AND conversation_id = ?', (file_id, conversation_id)).fetchone()
        if row is None:
            raise TreeError(404, 'Attachment not found in this conversation.')
        linked = connection.execute('SELECT message_id FROM message_files WHERE file_id = ?', (file_id,)).fetchall()
        if linked and not any(item['message_id'] == source_message_id for item in linked):
            raise TreeError(409, 'This attachment already belongs to a different user message.')
        rows.append(row)
    if sum(row['size'] for row in rows) > MAX_TOTAL_SIZE:
        raise TreeError(413, 'Attachments exceed the 25 MiB message limit.')
    for row in rows:
        connection.execute('INSERT INTO message_files VALUES (?, ?, ?)', (message_id, row['id'], conversation_id))


class FileStore:
    def __init__(self, connect, directory):
        self.connect, self.directory = connect, directory

    def path(self, key):
        if not STORAGE_KEY.fullmatch(key):
            raise TreeError(404, 'Invalid file identifier.')
        root = self.directory().resolve()
        path = root / key
        if path.is_symlink() or path.resolve().parent != root:
            raise TreeError(404, 'File unavailable.')
        return path

    def public(self, row):
        item = {key: row[key] for key in ('id', 'conversation_id', 'original_filename', 'mime_type', 'size', 'created_at')}
        item['available'] = self.path(row['storage_key']).is_file()
        item['has_preview'] = bool(row['preview_key'] and self.path(row['preview_key']).is_file())
        return item

    def find(self, cid, fid):
        with closing(self.connect()) as connection:
            row = connection.execute('SELECT * FROM files WHERE id = ? AND conversation_id = ?', (fid, cid)).fetchone()
            if row is None:
                raise TreeError(404, 'Attachment not found in this conversation.')
            return dict(row)

    def pending(self, cid):
        with closing(self.connect()) as connection:
            return [self.public(row) for row in connection.execute('''SELECT * FROM files
                WHERE conversation_id = ? AND NOT EXISTS (SELECT 1 FROM message_files WHERE file_id = files.id)
                ORDER BY created_at, id''', (cid,))]

    def decorate(self, messages):
        if not messages:
            return messages
        by_id = {message['id']: message for message in messages}
        for message in messages:
            message['attachments'] = []
        with closing(self.connect()) as connection:
            for row in connection.execute('''SELECT f.*, mf.message_id FROM files f JOIN message_files mf ON mf.file_id = f.id
                WHERE f.conversation_id = ? ORDER BY f.created_at, f.id''', (messages[0]['conversation_id'],)):
                if row['message_id'] in by_id:
                    by_id[row['message_id']]['attachments'].append(self.public(row))
        return messages

    async def upload(self, cid, fid, upload):
        # Upload IDs supplied as UUIDs make retry idempotent, including a lost
        # HTTP response after a successful database commit.
        try:
            return self.public(self.find(cid, fid))
        except TreeError as error:
            if error.status != 404:
                raise
        name = safe_name(upload.filename)
        self.directory().mkdir(parents=True, exist_ok=True)
        stem = fid.replace('-', '')
        temporary = self.path(stem + '.' + uuid4().hex + '.part')
        preview_temp = self.path(uuid4().hex + '.preview.png')
        final = self.path(stem + '.blob')
        preview = self.path(stem + '.preview.png')
        committed, moved = False, False
        try:
            size = 0
            with temporary.open('xb') as handle:
                while chunk := await upload.read(64 * 1024):
                    size += len(chunk)
                    if size > MAX_FILE_SIZE:
                        raise TreeError(413, 'Each file must be at most 10 MiB.')
                    handle.write(chunk)
            if not size:
                raise TreeError(422, 'Empty files are not supported.')
            # Validation happens only after storage, in a worker so decoding a
            # large image does not block streaming or cancellation requests.
            from starlette.concurrency import run_in_threadpool
            mime, has_preview = await run_in_threadpool(validate_file, temporary, name, upload.content_type, preview_temp)
            with closing(self.connect()) as connection, connection:
                connection.execute('BEGIN IMMEDIATE')
                if not connection.execute('SELECT 1 FROM conversations WHERE id = ?', (cid,)).fetchone():
                    raise TreeError(404, 'Conversation not found.')
                if connection.execute('SELECT 1 FROM cancelled_uploads WHERE id = ?', (fid,)).fetchone():
                    raise TreeError(409, 'This upload was removed. Choose the file again to upload it.')
                existing = connection.execute('SELECT * FROM files WHERE id = ?', (fid,)).fetchone()
                if existing:
                    if existing['conversation_id'] != cid:
                        raise TreeError(404, 'Attachment not found in this conversation.')
                    return self.public(existing)
                pending = connection.execute('''SELECT COUNT(*), COALESCE(SUM(size),0) FROM files
                    WHERE conversation_id = ? AND NOT EXISTS (SELECT 1 FROM message_files WHERE file_id = files.id)''', (cid,)).fetchone()
                if pending[0] >= MAX_FILES or pending[1] + size > MAX_TOTAL_SIZE:
                    raise TreeError(413, 'A draft can hold at most 5 files and 25 MiB in total.')
                temporary.replace(final)
                moved = True
                if has_preview:
                    preview_temp.replace(preview)
                connection.execute('''INSERT INTO files(id, conversation_id, original_filename, storage_key, mime_type, size, preview_key)
                    VALUES (?, ?, ?, ?, ?, ?, ?)''', (fid, cid, name, final.name, mime, size, preview.name if has_preview else None))
            committed = True
            return self.public(self.find(cid, fid))
        finally:
            temporary.unlink(missing_ok=True)
            preview_temp.unlink(missing_ok=True)
            if moved and not committed:
                final.unlink(missing_ok=True)
                preview.unlink(missing_ok=True)

    def remove(self, cid, fid):
        with closing(self.connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            if not connection.execute('SELECT 1 FROM conversations WHERE id = ?', (cid,)).fetchone():
                raise TreeError(404, 'Conversation not found.')
            row = connection.execute('SELECT conversation_id FROM files WHERE id = ?', (fid,)).fetchone()
            if row and row['conversation_id'] != cid:
                raise TreeError(404, 'Attachment not found in this conversation.')
            if connection.execute('SELECT 1 FROM message_files WHERE file_id = ?', (fid,)).fetchone():
                raise TreeError(409, 'Sent attachments are retained with their message branches.')
            connection.execute('INSERT OR IGNORE INTO cancelled_uploads VALUES (?, ?)', (fid, cid))
            connection.execute('DELETE FROM files WHERE id = ? AND conversation_id = ?', (fid, cid))
        self.cleanup()

    def cleanup(self, startup=False):
        self.directory().mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as connection, connection:
            connection.execute('''DELETE FROM files WHERE created_at < datetime('now', '-1 day')
                AND NOT EXISTS (SELECT 1 FROM message_files WHERE file_id = files.id)''')
        with closing(self.connect()) as connection:
            pending = [row[0] for row in connection.execute('SELECT storage_key FROM file_deletions')]
            known = {row[0] for row in connection.execute('SELECT storage_key FROM files UNION SELECT preview_key FROM files WHERE preview_key IS NOT NULL')}
        if startup:
            # Only at startup, before accepting requests: no live uploads can
            # be mistaken for crash leftovers. Never scan outside uploads/.
            pending += [path.name for path in self.directory().iterdir() if STORAGE_KEY.fullmatch(path.name) and path.name not in known]
        for key in set(pending):
            try:
                self.path(key).unlink(missing_ok=True)
            except OSError:
                continue  # Windows may still have an active download open.
            with closing(self.connect()) as connection, connection:
                connection.execute('DELETE FROM file_deletions WHERE storage_key = ?', (key,))
