"""Scoped file storage and extraction. Uploaded bytes are never executable assets."""
from contextlib import closing
from pathlib import Path
import csv
import hashlib
import io
import json
import re
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from uuid import uuid4
from PIL import Image
from pypdf import PdfReader

MAX_FILE = 10 * 1024 * 1024
MAX_TOTAL = 20 * 1024 * 1024
MAX_FILES = 4
MAX_CONVERSATION = 50 * 1024 * 1024
MAX_TEXT = 500_000
MIMES = {".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv",
         ".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
PARSER_VERSION = "extract-v1"

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
    """Return bounded sections with real page/row provenance; never invent pages."""
    data = path.read_bytes()
    sections = []
    if extension in (".txt", ".md"):
        text = decode_text(data)
        heading = ""
        for part in re.split(r"(?m)(?=^#{1,6} )", text) if extension == ".md" else [text]:
            if part.startswith("#"):
                heading = part.split("\n", 1)[0].lstrip("# ").strip()[:200]
            if part.strip():
                sections.append({"text": part.strip(), "section": heading})
    elif extension == ".csv":
        rows = csv.reader(io.StringIO(decode_text(data)), strict=True)
        headers = next(rows, None)
        if not headers or len(headers) > 100:
            raise ValueError("CSV needs a header and at most 100 columns.")
        for number, row in enumerate(rows, 2):
            if not row:
                continue
            if len(row) != len(headers):
                raise ValueError(f"CSV row {number} does not match its header.")
            text = " | ".join(f"{key}: {value}" for key, value in zip(headers, row))
            if len(text) > 1600:
                raise ValueError(f"CSV row {number} is too large; shorten its cells.")
            sections.append({"text": text, "section": f"CSV row {number}", "row": number})
    elif extension == ".pdf":
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise ValueError("Password-protected PDFs are not supported.")
        if len(reader.pages) > 200:
            raise ValueError("PDFs are limited to 200 pages.")
        for number, page in enumerate(reader.pages, 1):
            contents = page.get_contents()
            if contents and len(contents.get_data()) > 8 * 1024 * 1024:
                raise ValueError(f"PDF page {number} is too complex to process.")
            text = normalize(page.extract_text() or "")
            if text:
                sections.append({"text": text, "page": number, "section": f"Page {number}"})
            if sum(len(s["text"]) for s in sections) > MAX_TEXT:
                raise ValueError("Document exceeds the extracted-text limit.")
        if not sections:
            raise ValueError("No readable text found. Scanned PDFs need OCR, which this lesson does not implement.")
    elif extension == ".docx":
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(x.file_size for x in archive.infolist()) > 20 * 1024 * 1024:
                raise ValueError("DOCX expands beyond the extraction limit.")
            if any("vbaProject" in x.filename for x in archive.infolist()):
                raise ValueError("Macro-enabled documents are not supported.")
            raw = archive.read("word/document.xml")
            if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
                raise ValueError("DOCX contains unsupported XML declarations.")
            root = ET.fromstring(raw)
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            for number, paragraph in enumerate(root.findall(".//w:p", ns), 1):
                text = "".join(n.text or "" for n in paragraph.findall(".//w:t", ns))
                if text.strip():
                    sections.append({"text": normalize(text), "section": f"Paragraph {number}"})
    if not sections or not any(s["text"].strip() for s in sections):
        raise ValueError("The document contains no extractable text.")
    if sum(len(s["text"]) for s in sections) > MAX_TEXT:
        raise ValueError("Document exceeds 500,000 extracted characters.")
    return sections

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
                          WHERE state IN ('uploaded','extracting','chunking','embedding')""")
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
        item["available"] = self.path(row["stored_filename"]).is_file()
        return item

    def list(self, cid):
        with closing(self.connect()) as db:
            return [self.public(dict(r)) for r in db.execute("SELECT * FROM files WHERE conversation_id=? ORDER BY created_at,id", (cid,))]

    def accept(self, cid, name, declared_mime, data):
        name = clean_name(name)
        ext = Path(name).suffix.lower()
        if ext not in MIMES:
            raise ValueError("Supported files: TXT, MD, CSV, PDF, DOCX, PNG, JPEG and WebP.")
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
        elif ext == ".docx":
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    if "word/document.xml" not in archive.namelist():
                        raise ValueError("The uploaded file is not a DOCX document.")
            except zipfile.BadZipFile as error:
                raise ValueError("The uploaded file is not a DOCX document.") from error
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
            row = db.execute("SELECT state FROM files WHERE id=? AND conversation_id=?", (fid,cid)).fetchone()
            if not row:
                raise TreeError(404,"Selected source not found in this conversation.")
            if enabled and row["state"] != "ready":
                raise TreeError(409,"Wait for the selected files to finish indexing, or turn off Use files.")
        if enabled and not selected:
            raise TreeError(422,"Select at least one ready document or turn off Use files.")
        db.execute("INSERT INTO rag_turns VALUES (?,?,?)", (user_id,int(enabled),json.dumps(selected)))

    def decorate(self, tree):
        with closing(self.connect()) as db:
            for msg in tree["messages"]:
                files = db.execute("""SELECT f.* FROM files f JOIN message_files m ON m.file_id=f.id
                    WHERE m.message_id=? AND f.conversation_id=?""",(msg["id"],tree["conversation_id"])).fetchall()
                msg["attachments"] = [self.public(dict(f)) for f in files]
                msg["sources"] = [dict(r) for r in db.execute("""SELECT c.id, f.id file_id, f.original_filename, c.metadata
                    FROM citations s JOIN chunks c ON c.id=s.chunk_id JOIN files f ON f.id=c.file_id
                    WHERE s.message_id=? AND f.conversation_id=?""",(msg["id"],tree["conversation_id"]))]
        return tree
