"""Explicit compatible index repair from authoritative sources; never model substitution."""
import asyncio
from contextlib import closing
import json
import time

from starlette.concurrency import run_in_threadpool

from .vector_store import IndexInconsistent, IndexUnavailable, normalized


class VectorRecovery:
    def __init__(self, lifecycle, rag):
        self.life = lifecycle
        self.rag = rag

    def _current(self, lease, scope, sources, config):
        with closing(self.life.connect()) as db:
            db.execute("BEGIN")
            self.life.check(lease, db)
            actual, fingerprints, _ = self.life._sources(db, scope.conversation_id, scope.file_id)
            file = db.execute("SELECT config FROM files WHERE id=?", (scope.file_id,)).fetchone()
            if actual != scope or fingerprints != sources or file["config"] != config:
                raise IndexInconsistent("Document changed during index repair; retry against current sources.")

    async def rebuild_document(self, cid, fid):
        """Repair one document, bounded to 4,000 sources and 30 minutes.

        A healthy generation is reused. Incomplete writes are recovered without
        inference. Regeneration requires the exact recorded model digest and
        representation. No canonical document or citation identities are changed.
        """
        client = None
        operation = scope = space = None
        with self.life.operation("rebuild", ttl=1800) as lease:
            if lease.backend != "lancedb":
                raise IndexUnavailable("Index repair requires an active dedicated backend.")
            try:
                async with asyncio.timeout(1800):
                    with closing(self.life.connect()) as db:
                        db.execute("BEGIN")
                        self.life.check(lease, db)
                        scope, sources, rows = self.life._sources(db, cid, fid)
                        file = dict(db.execute("SELECT * FROM files WHERE id=? AND conversation_id=?", (fid,cid)).fetchone())
                        published = db.execute("SELECT space,manifest FROM vector_generations WHERE file_id=? AND generation=? AND manifest IS NOT NULL",
                                               (fid,scope.generation)).fetchall()
                    if not 1 <= len(rows) <= 4000:
                        raise IndexUnavailable("Repair requires 1-4,000 authoritative source records.")
                    config = file["config"]
                    space = self.rag.embedding_space(config, file["dimension"])
                    published = [row for row in published if row["space"] == space.key]
                    if any(row["config"] != config for row in rows):
                        raise IndexInconsistent("Source embedding configurations disagree; explicit re-indexing is required.")
                    # Full numeric manifest validation, not just source identity checks.
                    try:
                        snapshots = await run_in_threadpool(self.life.snapshot, lease, cid, [fid], space)
                        await run_in_threadpool(self.life.store.verify_index_consistency, space, scope, snapshots[0][1])
                        self._current(lease, scope, sources, config)
                        with closing(self.life.connect()) as db, db:
                            db.execute("UPDATE files SET state='ready',error=NULL WHERE id=? AND conversation_id=? AND index_generation=?",
                                       (fid,cid,scope.generation))
                        return {"file_id":fid, "status":"reused", "sources":len(rows), "embedding_calls":0}
                    except IndexUnavailable:
                        pass

                    # Commit retryable intent BEFORE any embedding inference.
                    with self.life.gate.hold(), closing(self.life.connect()) as db, db:
                        db.execute("BEGIN IMMEDIATE")
                        operation, staged_scope, _ = self.life.stage_transaction(db, lease, cid, fid, space)
                        if staged_scope != scope:
                            raise IndexInconsistent("Document changed before repair began.")
                        db.execute("UPDATE vector_generations SET state='pending' WHERE file_id=? AND generation=? AND space=?",
                                   (fid,scope.generation,space.key))
                        db.execute("UPDATE vector_operations SET state='pending',error=NULL,updated_at=? WHERE id=?",
                                   (time.time(),operation))
                        db.execute("UPDATE files SET state='indexing',error=NULL WHERE id=?", (fid,))
                    try:
                        receipt = await run_in_threadpool(self.life.store.recover_generation, space, scope, sources)
                        if published and not any(json.loads(row["manifest"]) == receipt["manifest"] for row in published):
                            raise IndexInconsistent("Existing vectors disagree with the recorded published manifest.")
                        calls = 0
                    except IndexUnavailable:
                        client = self.rag.provider.client(timeout=120)
                        if await self.rag.descriptor(client, refresh=True) != config:
                            raise IndexUnavailable("Required embedding space is unavailable. Restore its exact model digest; authoritative document reading remains available.")
                        calls = 0
                        for start in range(0,len(rows),12):
                            self.life.renew(lease,1800)
                            self._current(lease,scope,sources,config)
                            batch = rows[start:start+12]
                            result = await client.embed(model=space.model,truncate=False,
                                input=[self.rag.provider.document_input(file["original_filename"]+" "+
                                    str(row["metadata"].get("section","")),row["text"]) for row in batch])
                            vectors = result["embeddings"]
                            if len(vectors) != len(batch):
                                raise IndexInconsistent("Embedding provider returned the wrong number of vectors.")
                            for row, vector in zip(batch,vectors):
                                row["vector"] = normalized(vector,space.dimensions)
                            calls += 1
                            await asyncio.sleep(0)
                        if await self.rag.descriptor(client,refresh=True) != config:
                            raise IndexUnavailable("Embedding model changed during repair; generation was not published.")
                        self._current(lease,scope,sources,config)
                        await run_in_threadpool(self.life.store.initialize_index,space)
                        receipt = await run_in_threadpool(self.life.store.upsert_chunks,space,scope,rows)
                    self._current(lease,scope,sources,config)
                    await run_in_threadpool(self.life.publish,lease,operation,space,receipt)
                    # A mutation after publication must not make a newer generation ready.
                    with closing(self.life.connect()) as db, db:
                        db.execute("UPDATE files SET state='ready',error=NULL WHERE id=? AND conversation_id=? AND index_generation=?",
                                   (fid,cid,scope.generation))
                    return {"file_id":fid,"status":"rebuilt" if calls else "recovered",
                            "sources":len(rows),"embedding_calls":calls,"generation":scope.generation}
            except BaseException as error:
                if operation is not None:
                    with closing(self.life.connect()) as db, db:
                        db.execute("UPDATE vector_operations SET state='pending',error=?,updated_at=? WHERE id=?",
                                   (type(error).__name__,time.time(),operation))
                        db.execute("UPDATE vector_generations SET state='degraded' WHERE file_id=? AND generation=? AND space=?",
                                   (fid,scope.generation,space.key))
                        db.execute("UPDATE files SET state='failed',error=? WHERE id=? AND conversation_id=? AND index_generation=?",
                                   ("Index repair incomplete; authoritative parsed content is preserved.",fid,cid,scope.generation))
                raise
            finally:
                if client is not None:
                    await client.close()
