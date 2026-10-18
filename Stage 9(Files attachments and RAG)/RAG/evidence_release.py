"""Revision-bound output release, shared with supported source mutations.

The linearization boundary is a bounded ASGI body send while holding the
conversation's OS gate. Mutations commit only outside that reservation. This
records server-emitted bytes, not client receipt or browser display.
"""
import asyncio
from contextlib import asynccontextmanager, closing
from dataclasses import dataclass
import hashlib
import json
import os
import time

from .storage_gate import ProcessGate, StorageBusy

SEND_TIMEOUT = 5.0
QUEUE_SIZE = 8
DELTA_CHARS = 2048


class SourceInvalidated(ValueError):
    def __init__(self):
        super().__init__("A selected source changed or became unavailable. This response was interrupted; retry using the current files.")


def gate_for(docs, cid):
    if type(cid) is not int or cid <= 0:
        raise ValueError("Invalid conversation scope.")
    return ProcessGate(docs.data_dir() / "coord" / "release" / (str(cid) + ".lock"))


@asynccontextmanager
async def release_gate(docs, cid, timeout=10):
    # Do not hold a thread-owned RLock across await. Independent OS handles
    # contend with ProcessGate in both this process and other supported workers.
    path = gate_for(docs, cid).path
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")
    locked = False
    deadline = time.monotonic() + timeout
    try:
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        while not locked:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    raise StorageBusy("Source release reservation timed out.")
                await asyncio.sleep(.01)
        yield
    finally:
        if locked:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


@dataclass(frozen=True)
class EvidenceRevision:
    conversation_id: int
    user_message_id: int
    # File identity, owner, content, canonical/index revision and parser version.
    files: tuple
    identity: str

    @staticmethod
    def read(db, cid, ids):
        rows = []
        for fid in ids:
            row = db.execute("""SELECT id,conversation_id,sha256,index_generation,parser_config
                FROM files WHERE id=? AND conversation_id=?""", (fid, cid)).fetchone()
            if row is None:
                raise SourceInvalidated()
            rows.append(tuple(row))
        return tuple(rows)

    @classmethod
    def capture(cls, docs, cid, mid, file_ids):
        if not file_ids or len(file_ids) > 40 or len(set(file_ids)) != len(file_ids):
            raise SourceInvalidated()
        with gate_for(docs, cid).hold(), closing(docs.connect()) as db:
            db.execute("BEGIN")
            files = cls.read(db, cid, file_ids)
            identity = hashlib.sha256(json.dumps([cid, mid, files], sort_keys=True).encode()).hexdigest()
            result = cls(cid, mid, files, identity)
            result.validate(docs, db=db)
            return result

    def validate(self, docs, db=None):
        if db is None:
            with closing(docs.connect()) as connection:
                connection.execute("BEGIN")
                return self.validate(docs, connection)
        ids = [f[0] for f in self.files]
        if self.read(db, self.conversation_id, ids) != self.files:
            raise SourceInvalidated()
        turn = db.execute("""SELECT r.use_files,r.file_ids FROM rag_turns r
            JOIN messages m ON m.id=r.message_id WHERE r.message_id=? AND m.conversation_id=?""",
            (self.user_message_id, self.conversation_id)).fetchone()
        if not turn or not turn["use_files"] or not set(ids).issubset(json.loads(turn["file_ids"])):
            raise SourceInvalidated()

    def public(self):
        return {"version": 1, "identity": self.identity, "files": [
            {"file_id": f[0], "fingerprint": f[2], "generation": f[3], "parser": f[4]}
            for f in self.files]}


class OutputQueue(asyncio.Queue):
    def __init__(self, job):
        super().__init__(maxsize=QUEUE_SIZE)
        self.job = job

    def put_nowait(self, event):
        event = dict(event)
        if event.get("type") in ("status", "error"):
            event["message"] = str(event.get("message", ""))[:1024]
        if self.full() and event.get("type") == "status":
            return  # Intermediate progress is coalesced; text is never dropped here.
        revision = self.job.get("evidence_revision")
        if revision and event.get("type") in ("delta", "status"):
            event["_revision"] = revision.identity
        return super().put_nowait(event)


def validate_job(docs, job):
    if job.get("invalidated"):
        raise SourceInvalidated()
    revision = job.get("evidence_revision")
    if revision:
        revision.validate(docs)
    if job.get('drawing_versions'):
        from .drawings import Drawings
        Drawings(docs).check(job['conversation_id'],job['drawing_versions'])


async def queue_delta(docs, job, text):
    for offset in range(0, len(text), DELTA_CHARS):
        validate_job(docs, job)
        acknowledged = asyncio.get_running_loop().create_future()
        await job["queue"].put({"type": "delta", "content": text[offset:offset+DELTA_CHARS],
                                "_ack": acknowledged})
        # A generated token is not yet a server-emitted token. Limit how far
        # generation can run ahead, and make Stop cancel this wait.
        await acknowledged


async def watch_sources(docs, job):
    while True:
        await asyncio.sleep(.2)
        if job.get("finishing"):
            return
        try:
            validate_job(docs, job)
        except SourceInvalidated:
            job["invalidated"] = True
            job["cancelling"] = True
            job["task"].cancel()
            return


def discard_pending(job):
    while True:
        try:
            event = job["queue"].get_nowait()
        except asyncio.QueueEmpty:
            return
        future = event.get("_ack")
        if future is not None and not future.done():
            future.cancel()
        job["queue"].task_done()
