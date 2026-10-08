"""Scoped file storage and extraction. Uploaded bytes are never executable assets."""
from contextlib import closing, nullcontext
from pathlib import Path
import os
import sqlite3
import hashlib
import io
import json
import re
import unicodedata
import zipfile
from uuid import uuid4
from PIL import Image

MAX_FILE = 10 * 1024 * 1024
MAX_TOTAL = 20 * 1024 * 1024
MAX_FILES = 4
MAX_CONVERSATION = 50 * 1024 * 1024
MIMES = {".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv",
         ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
         ".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
from .document_parsers import native_sections, VERSION as PARSER_VERSION

def clean_name(value):
    value = unicodedata.normalize("NFC", (value or "file").replace("\\", "/").split("/")[-1])
    return re.sub(r'[\x00-\x1f\x7f<>:"|?*\u202a-\u202e\u2066-\u2069]', "_", value).strip(" .")[:180] or "file"

def normalize(text):
    return unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n")).strip()

def decode_text(data):
    try:
        text = data.decode("utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")
    except UnicodeError as error:
        raise ValueError("Save text documents as UTF-8 or UTF-16 before uploading.") from error
    if any(ord(c) < 32 and c not in "\n\r\t" for c in text):
        raise ValueError("This is binary data, not a supported text document.")
    return normalize(text)

def extract(path, extension):
    return native_sections(path, extension, decode_text, normalize)


class Documents:
    def __init__(self, connect, data_dir):
        self.connect, self.data_dir = connect, data_dir

    @property
    def uploads(self):
        return self.data_dir() / "uploads"

    def init(self):
        self.uploads.mkdir(parents=True, exist_ok=True)
        (self.uploads / ".tmp").mkdir(exist_ok=True)
        with closing(self.connect()) as db, db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS files(
              id TEXT PRIMARY KEY, conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
              original_filename TEXT NOT NULL, stored_filename TEXT NOT NULL UNIQUE,
              mime_type TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
              extension TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'uploaded', error TEXT,
              config TEXT, dimension INTEGER, extracted TEXT, indexed_at TEXT, indexing_ms REAL,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              UNIQUE(conversation_id, sha256));
            CREATE TABLE IF NOT EXISTS message_files(
              message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
              file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
              PRIMARY KEY(message_id, file_id));
            CREATE TABLE IF NOT EXISTS rag_turns(
              message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
              use_files INTEGER NOT NULL, file_ids TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS chunks(
              id TEXT PRIMARY KEY, file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
              chunk_index INTEGER NOT NULL, text TEXT NOT NULL, metadata TEXT NOT NULL,
              vector TEXT NOT NULL, config TEXT NOT NULL, UNIQUE(file_id,chunk_index));
            CREATE INDEX IF NOT EXISTS idx_files_chat ON files(conversation_id);
            CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file_id);
            CREATE TABLE IF NOT EXISTS citations(
              message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
              chunk_id TEXT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
              PRIMARY KEY(message_id, chunk_id));
            CREATE TABLE IF NOT EXISTS rag_runs(
              message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
              details TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS pending_unlinks(stored_filename TEXT PRIMARY KEY);
            CREATE TRIGGER IF NOT EXISTS files_cleanup AFTER DELETE ON files BEGIN
              INSERT OR IGNORE INTO pending_unlinks VALUES (OLD.stored_filename);
            END;
            CREATE TRIGGER IF NOT EXISTS message_file_scope BEFORE INSERT ON message_files
              WHEN (SELECT conversation_id FROM messages WHERE id=NEW.message_id)
                   != (SELECT conversation_id FROM files WHERE id=NEW.file_id)
              BEGIN SELECT RAISE(ABORT, 'Attachment belongs to another conversation'); END;
            """)
            db.execute("""UPDATE files SET state='failed', error='Indexing was interrupted. Choose Re-index.'
                          WHERE state IN ('uploaded','extracting','chunking','embedding','vision')""")
        self.upgrade()
        self.cleanup()

    def cleanup(self):
        """Transactional deletion outbox retries failed unlinks, including after a crash."""
        with closing(self.connect()) as db, db:
            for row in db.execute("SELECT stored_filename FROM pending_unlinks").fetchall():
                try:
                    self.path(row[0]).unlink(missing_ok=True)
                except OSError:
                    continue
                db.execute("DELETE FROM pending_unlinks WHERE stored_filename=?", (row[0],))
            known = {r[0] for r in db.execute("SELECT stored_filename FROM files")}
        import time
        for path in [*self.uploads.glob("*"), *(self.uploads / ".tmp").glob("*")]:
            if path.is_file() and path.name not in known and time.time() - path.stat().st_mtime > 86400:
                path.unlink(missing_ok=True)

    def path(self, key):
        if not re.fullmatch(r"[a-f0-9]{32}\.[a-z0-9]+", key):
            raise ValueError("Invalid storage key.")
        return self.uploads / key

    def row(self, cid, fid):
        with closing(self.connect()) as db:
            row = db.execute("SELECT * FROM files WHERE id=? AND conversation_id=?", (fid, cid)).fetchone()
            return dict(row) if row else None

    def public(self, row):
        keys = ("id", "conversation_id", "original_filename", "mime_type", "size", "state", "error",
                "created_at", "indexed_at", "indexing_ms")
        item = {k: row[k] for k in keys}
        item["parse_state"] = row.get("parse_state","pending")
        item["coverage"] = json.loads(row.get("coverage") or "{}")
        item["capabilities"] = {"tools":item["parse_state"] in ("ready","partial") and bool(row.get("extracted")),
            "dense":row["state"]=="ready","vision":row["mime_type"].startswith("image/")}
        item["available"] = self.path(row["stored_filename"]).is_file()
        return item

    def list(self, cid):
        with closing(self.connect()) as db:
            return [self.public(dict(r)) for r in db.execute("SELECT * FROM files WHERE conversation_id=? ORDER BY created_at,id", (cid,))]

    def accept(self, cid, name, declared_mime, data):
        name = clean_name(name)
        ext = Path(name).suffix.lower()
        if ext not in MIMES:
            raise ValueError("Supported files: TXT, MD, CSV, XLSX, PDF, DOCX, PNG, JPEG and WebP.")
        if not data or len(data) > MAX_FILE:
            raise ValueError("Each file must be nonempty and at most 10 MiB.")
        mime = MIMES[ext]
        allowed = {mime, "", "application/octet-stream"}
        if ext in (".txt", ".md", ".csv"):
            allowed |= {"text/plain", "text/markdown", "text/csv", "application/vnd.ms-excel"}
        if (declared_mime or "").split(";")[0].lower() not in allowed:
            raise ValueError("The declared MIME type does not match this file.")
        if ext in (".txt", ".md", ".csv"):
            decode_text(data)
        elif ext == ".pdf" and not data.startswith(b"%PDF-"):
            raise ValueError("The uploaded file is not a PDF.")
        elif ext in (".docx", ".xlsx"):
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    if ("word/document.xml" if ext==".docx" else "xl/workbook.xml") not in archive.namelist():
                        raise ValueError("The uploaded file is not a valid Office document.")
            except zipfile.BadZipFile as error:
                raise ValueError("The uploaded file is not a valid Office document.") from error
        elif mime.startswith("image/"):
            try:
                with Image.open(io.BytesIO(data)) as im:
                    if Image.MIME.get(im.format) != mime or im.width * im.height > 20_000_000:
                        raise ValueError("Image format or dimensions are unsupported.")
                    im.verify()
                # Re-encode to remove metadata and trailing/polyglot payloads.
                with Image.open(io.BytesIO(data)) as im:
                    output = io.BytesIO()
                    im.save(output, format=im.format)
                    data = output.getvalue()
            except Exception as error:
                raise ValueError("Invalid image. Use PNG, JPEG or WebP under 20 megapixels.") from error
        digest, fid = hashlib.sha256(data).hexdigest(), uuid4().hex
        key = fid + ext
        target = self.path(key)
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM conversations WHERE id=?", (cid,)).fetchone():
                raise ValueError("Conversation no longer exists.")
            previous = db.execute("SELECT * FROM files WHERE conversation_id=? AND sha256=?", (cid,digest)).fetchone()
            if previous:
                return self.public(dict(previous)), False
            total, count = db.execute("SELECT COALESCE(SUM(size),0), COUNT(*) FROM files WHERE conversation_id=?", (cid,)).fetchone()
            if total + len(data) > MAX_CONVERSATION or count >= 40:
                raise ValueError("Conversation limit: 50 MiB and 40 files. Remove a document first.")
            if len(data) > MAX_FILE:
                raise ValueError("Decoded image exceeds 10 MiB.")
            try:
                with target.open("xb") as output:
                    output.write(data)
                db.execute("""INSERT INTO files(id,conversation_id,original_filename,stored_filename,mime_type,size,sha256,extension,state,error)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (fid,cid,name,key,mime,len(data),digest,ext,
                     "preview" if mime.startswith("image/") else "uploaded",
                     "Preview/download only: image OCR and vision are not enabled." if mime.startswith("image/") else None))
                db.commit()
            except BaseException:
                target.unlink(missing_ok=True)
                raise
        return self.public(self.row(cid,fid)), True

    def replace(self, cid, old_id, name, mime, data):
        old = self.row(cid, old_id)
        if not old:
            raise ValueError("File no longer exists.")
        result, created = self.accept(cid, name, mime, data)
        if result["id"] == old_id:
            return result, False
        try:
            with closing(self.connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("""INSERT OR IGNORE INTO message_files SELECT message_id,? FROM message_files
                    WHERE file_id=?""", (result["id"], old_id))
                for turn in db.execute("""SELECT r.* FROM rag_turns r JOIN messages m ON m.id=r.message_id
                    WHERE m.conversation_id=?""", (cid,)).fetchall():
                    ids = list(dict.fromkeys(result["id"] if fid == old_id else fid for fid in json.loads(turn["file_ids"])))
                    db.execute("UPDATE rag_turns SET file_ids=? WHERE message_id=?", (json.dumps(ids),turn["message_id"]))
                db.execute("DELETE FROM files WHERE id=? AND conversation_id=?", (old_id,cid))
                db.execute("DELETE FROM rag_runs WHERE message_id IN (SELECT id FROM messages WHERE conversation_id=?)",(cid,))
        except BaseException:
            if created:
                with closing(self.connect()) as db, db:
                    db.execute("DELETE FROM files WHERE id=?", (result["id"],))
            self.cleanup()
            raise
        if hasattr(self,"vector_cache"):self.vector_cache.invalidate(cid,old_id)
        self.cleanup()
        return result, created

    def bind(self, db, request, user_id, target):
        """Runs inside the tree reservation transaction, before generation is accepted."""
        from tree_store import TreeError
        cid = request.conversation_id
        if request.action not in ("send","edit"):
            return
        ids = list(dict.fromkeys(request.attachment_ids))
        if request.action == "edit":
            ids = [r[0] for r in db.execute("SELECT file_id FROM message_files WHERE message_id=?", (target["id"],))]
        if len(ids) > MAX_FILES:
            raise TreeError(422,"At most four attachments are allowed per message.")
        total = 0
        for fid in ids:
            row = db.execute("SELECT * FROM files WHERE id=? AND conversation_id=?", (fid,cid)).fetchone()
            if not row:
                raise TreeError(404,"Attachment not found in this conversation.")
            total += row["size"]
            if not self.path(row["stored_filename"]).is_file():
                raise TreeError(409,"An attachment is unavailable. Upload it again.")
            db.execute("INSERT INTO message_files VALUES (?,?)", (user_id,fid))
        if total > MAX_TOTAL:
            raise TreeError(422,"Attachments total more than 20 MiB.")
        selected = list(dict.fromkeys(request.file_ids))
        enabled = request.use_files
        if request.action == "edit":
            old = db.execute("SELECT * FROM rag_turns WHERE message_id=?", (target["id"],)).fetchone()
            if old:
                selected, enabled = json.loads(old["file_ids"]), bool(old["use_files"])
        for fid in selected:
            row = db.execute("SELECT state,parse_state FROM files WHERE id=? AND conversation_id=?", (fid,cid)).fetchone()
            if not row:
                raise TreeError(404,"Selected source not found in this conversation.")
            if enabled and row["parse_state"] not in ("ready","partial"):
                raise TreeError(409,"Wait for extraction or re-index the selected files, or turn off Use files.")
        if enabled and not selected:
            raise TreeError(422,"Select at least one ready document or turn off Use files.")
        db.execute("INSERT INTO rag_turns VALUES (?,?,?)", (user_id,int(enabled),json.dumps(selected)))

    def decorate(self, tree):
        with closing(self.connect()) as db:
            for msg in tree["messages"]:
                files = db.execute("""SELECT f.* FROM files f JOIN message_files m ON m.file_id=f.id
                    WHERE m.message_id=? AND f.conversation_id=?""",(msg["id"],tree["conversation_id"])).fetchall()
                msg["attachments"] = [self.public(dict(f)) for f in files]
                msg["sources"] = [dict(r) for r in db.execute("""SELECT c.id, f.id file_id, f.original_filename, c.metadata, s.ordinal
                    FROM citations s JOIN chunks c ON c.id=s.chunk_id JOIN files f ON f.id=c.file_id
                    WHERE s.message_id=? AND f.conversation_id=? ORDER BY s.ordinal,c.id""",(msg["id"],tree["conversation_id"]))]
                for row in db.execute("""SELECT b.id,b.file_id,b.data,s.ordinal,f.original_filename FROM block_citations s
                    JOIN document_blocks b ON b.id=s.block_id JOIN files f ON f.id=b.file_id
                    WHERE s.message_id=? AND f.conversation_id=? ORDER BY s.ordinal,b.id""",(msg["id"],tree["conversation_id"])):
                    block=json.loads(row["data"])
                    msg["sources"].append({"id":row["id"].replace("BLOCK_","SOURCE_"),"file_id":row["file_id"],
                        "original_filename":row["original_filename"],"metadata":json.dumps({k:v for k,v in block.items() if k!="text"}),"ordinal":row["ordinal"]})
                msg["sources"].sort(key=lambda source:(source["ordinal"],source["id"]))
                msg["tool_evidence"]=[dict(r) for r in db.execute("SELECT id,ordinal FROM tool_runs WHERE message_id=? AND conversation_id=? ORDER BY ordinal",
                    (msg["id"],tree["conversation_id"]))]
        return tree

    def backup_if_needed(self):
        database=self.data_dir()/"chat.db"
        if not database.is_file():
            return
        with closing(sqlite3.connect(database)) as db:
            exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='document_schema'").fetchone()
            if exists and db.execute("SELECT 1 FROM document_schema WHERE version=2").fetchone():
                return
            backup=self.data_dir()/"chat.pre-document-intelligence.db"
            if not backup.exists():
                with closing(sqlite3.connect(backup)) as copy:
                    db.backup(copy)

    def upgrade(self):
        statements=[
            "CREATE TABLE IF NOT EXISTS document_schema(version INTEGER PRIMARY KEY)",
            """CREATE TABLE IF NOT EXISTS document_blocks(id TEXT PRIMARY KEY,
                file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                ordinal INTEGER NOT NULL, data TEXT NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS idx_blocks_file ON document_blocks(file_id,ordinal)",
            """CREATE TABLE IF NOT EXISTS document_cache(file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                cache_key TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(file_id,cache_key))""",
            """CREATE TABLE IF NOT EXISTS history_summaries(conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                digest TEXT NOT NULL, summary TEXT NOT NULL, PRIMARY KEY(conversation_id,digest))""",
            """CREATE TABLE IF NOT EXISTS tool_runs(id TEXT PRIMARY KEY, message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE, ordinal INTEGER NOT NULL, data TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS tool_run_files(run_id TEXT NOT NULL REFERENCES tool_runs(id) ON DELETE CASCADE,
                file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE, PRIMARY KEY(run_id,file_id))""",
            """CREATE TRIGGER IF NOT EXISTS invalidate_file_tools BEFORE DELETE ON files BEGIN
                DELETE FROM tool_runs WHERE id IN (SELECT run_id FROM tool_run_files WHERE file_id=OLD.id); END""",
            """CREATE TABLE IF NOT EXISTS block_citations(message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                block_id TEXT NOT NULL REFERENCES document_blocks(id) ON DELETE CASCADE, ordinal INTEGER NOT NULL,
                PRIMARY KEY(message_id,block_id))""",
        ]
        with closing(self.connect()) as db,db:
            db.execute("BEGIN IMMEDIATE")
            for statement in statements:db.execute(statement)
            fields={r["name"] for r in db.execute("PRAGMA table_info(files)")}
            for name,definition in (("parse_state","TEXT NOT NULL DEFAULT 'pending'"),
                                    ("coverage","TEXT NOT NULL DEFAULT '{}'"),("parser_config","TEXT"),("index_generation","INTEGER NOT NULL DEFAULT 0")):
                if name not in fields:db.execute(f"ALTER TABLE files ADD COLUMN {name} {definition}")
            # Counters change in the same transaction as every indexed representation mutation.
            for table in ("chunks","document_blocks"):
                for event,ref in (("INSERT","NEW"),("UPDATE","NEW"),("DELETE","OLD")):
                    db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_generation_{event.lower()} AFTER {event} ON {table} BEGIN UPDATE files SET index_generation=index_generation+1 WHERE id={ref}.file_id; END")
            db.execute("""CREATE TRIGGER IF NOT EXISTS file_generation_config AFTER UPDATE OF config,parser_config,sha256 ON files
                WHEN NEW.config IS NOT OLD.config OR NEW.parser_config IS NOT OLD.parser_config OR NEW.sha256 IS NOT OLD.sha256
                BEGIN UPDATE files SET index_generation=index_generation+1 WHERE id=NEW.id; END""")
            fields={r["name"] for r in db.execute("PRAGMA table_info(citations)")}
            if "ordinal" not in fields:
                db.execute("ALTER TABLE citations ADD COLUMN ordinal INTEGER NOT NULL DEFAULT 0")
                mids=[r[0] for r in db.execute("SELECT DISTINCT message_id FROM citations")]
                for mid in mids:
                    text=db.execute("SELECT content FROM messages WHERE id=?",(mid,)).fetchone()[0]
                    rows=[r[0] for r in db.execute("SELECT chunk_id FROM citations WHERE message_id=?",(mid,))]
                    rows.sort(key=lambda cid:(text.find("["+cid+"]") if "["+cid+"]" in text else len(text),cid))
                    for i,cid in enumerate(rows):
                        db.execute("UPDATE citations SET ordinal=? WHERE message_id=? AND chunk_id=?",(i,mid,cid))
            # Old sources remain available for historical citations, but cannot be retrieved by the new pipeline.
            db.execute("""UPDATE files SET state='reindex_required',parse_state='legacy'
                WHERE state='ready' AND (parser_config IS NULL OR parser_config!=?)""",(PARSER_VERSION,))
            if not os.getenv("RAG_DEBUG","0")=="1":
                for row in db.execute("SELECT message_id,details FROM rag_runs").fetchall():
                    data=json.loads(row["details"]);data.pop("context",None)
                    db.execute("UPDATE rag_runs SET details=? WHERE message_id=?",(json.dumps(data),row["message_id"]))
            db.execute("INSERT OR IGNORE INTO document_schema VALUES(2)")
            if db.execute("PRAGMA foreign_key_check").fetchone():
                raise RuntimeError("Document migration foreign-key check failed.")
        self.fts_available=False
        try:
            with closing(self.connect()) as db,db:
                db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(id UNINDEXED,text)")
                for statement in (
                    """CREATE TRIGGER IF NOT EXISTS chunks_fts_insert AFTER INSERT ON chunks BEGIN
                       INSERT INTO chunks_fts(id,text) VALUES(NEW.id,NEW.text); END""",
                    """CREATE TRIGGER IF NOT EXISTS chunks_fts_delete AFTER DELETE ON chunks BEGIN
                       DELETE FROM chunks_fts WHERE id=OLD.id; END""",
                    """CREATE TRIGGER IF NOT EXISTS chunks_fts_update AFTER UPDATE OF text ON chunks BEGIN
                       DELETE FROM chunks_fts WHERE id=OLD.id; INSERT INTO chunks_fts(id,text) VALUES(NEW.id,NEW.text); END"""):
                    db.execute(statement)
                db.execute("INSERT INTO chunks_fts(id,text) SELECT id,text FROM chunks WHERE id NOT IN (SELECT id FROM chunks_fts)")
            self.fts_available=True
        except sqlite3.OperationalError:
            # FTS is optional; retrieval has a bounded corpus-scoped lexical fallback.
            self.fts_available=False

    def store_extracted(self,cid,fid,sections,warning=""):
        from .document_tools import stable_blocks
        blocks=stable_blocks(fid,sections)
        missing=[b.get("page",b.get("section")) for b in blocks if b["type"]=="unreadable"]
        coverage={"complete":not missing,"unreadable":missing,"vision":any(b.get("origin")=="vision" for b in blocks),"warning":warning}
        with closing(self.connect()) as db,db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM files WHERE id=? AND conversation_id=?",(fid,cid)).fetchone():return
            old_ids={r[0] for r in db.execute("SELECT id FROM document_blocks WHERE file_id=?",(fid,))}
            ids={b["id"] for b in blocks}
            if old_ids and old_ids != ids:
                # Invalidate obsolete passage previews immediately, even if the
                # subsequent embedding call fails. Unchanged blocks survive.
                stale=[r["id"] for r in db.execute("SELECT id,metadata FROM chunks WHERE file_id=?",(fid,))
                    if json.loads(r["metadata"]).get("id") not in ids]
                db.executemany("DELETE FROM chunks WHERE id=?",((sid,) for sid in stale))
                db.execute("UPDATE files SET config=NULL WHERE id=?",(fid,))
                db.execute("DELETE FROM tool_runs WHERE id IN (SELECT run_id FROM tool_run_files WHERE file_id=?)",(fid,))
                db.execute("DELETE FROM document_cache WHERE file_id=? AND cache_key LIKE 'summary-%'",(fid,))
            for b in blocks:
                db.execute("""INSERT INTO document_blocks VALUES(?,?,?,?) ON CONFLICT(id)
                    DO UPDATE SET ordinal=excluded.ordinal,data=excluded.data""",(b["id"],fid,b["block_index"],json.dumps(b)))
            db.executemany("DELETE FROM document_blocks WHERE id=?",((bid,) for bid in old_ids-ids))
            db.execute("UPDATE files SET extracted=?,parse_state=?,coverage=?,parser_config=? WHERE id=?",
                (json.dumps(sections),"ready" if not missing else "partial",json.dumps(coverage),PARSER_VERSION,fid))
        return blocks

    def blocks(self,cid,fid):
        if not self.row(cid,fid):raise ValueError("File not found in this conversation.")
        with closing(self.connect()) as db:
            return [json.loads(r[0]) for r in db.execute("SELECT data FROM document_blocks WHERE file_id=? ORDER BY ordinal",(fid,))]

    def cache_get(self,fid,key):
        with closing(self.connect()) as db:
            row=db.execute("SELECT data FROM document_cache WHERE file_id=? AND cache_key=?",(fid,key)).fetchone()
            return json.loads(row[0]) if row else None

    def cache_put(self,fid,key,data):
        with closing(self.connect()) as db,db:
            if db.execute("SELECT 1 FROM files WHERE id=?",(fid,)).fetchone():
                db.execute("INSERT OR REPLACE INTO document_cache VALUES(?,?,?)",(fid,key,json.dumps(data)))

    def persist_tools(self,mid,cid,records,db=None):
        owned=db is None
        with closing(self.connect()) if owned else nullcontext(db) as db, db if owned else nullcontext():
            for ordinal,record in enumerate(records):
                tid="TOOL_"+hashlib.sha256((str(mid)+json.dumps(record,sort_keys=True)).encode()).hexdigest()[:24]
                db.execute("INSERT OR REPLACE INTO tool_runs VALUES(?,?,?,?,?)",(tid,mid,cid,ordinal,json.dumps(record)))
                for fid in record["files"]:
                    if not db.execute("SELECT 1 FROM files WHERE id=? AND conversation_id=?",(fid,cid)).fetchone():
                        raise ValueError("Tool evidence file is outside this conversation.")
                    db.execute("INSERT OR IGNORE INTO tool_run_files VALUES(?,?)",(tid,fid))
