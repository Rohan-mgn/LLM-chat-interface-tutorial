"""Explicit recovery preserves source identities and never substitutes a model."""
import asyncio
from contextlib import closing
import importlib.util
import json
import unittest
from unittest.mock import patch

import check_vector_runtime as fixtures
from RAG.vector_recovery import VectorRecovery
from RAG.vector_store import IndexUnavailable, IndexInconsistent


@unittest.skipUnless(importlib.util.find_spec("lancedb"),"Install requirements-vector-dev.txt")
class RecoveryTests(unittest.TestCase):
    setUp=fixtures.RuntimeTests.setUp
    tearDown=fixtures.RuntimeTests.tearDown
    upload=fixtures.RuntimeTests.upload
    send=fixtures.RuntimeTests.send
    rows=fixtures.RuntimeTests.rows

    def recovery(self):
        return VectorRecovery(self.app.rag.lifecycle,self.app.rag)

    def remove_vectors(self):
        life=self.app.rag.lifecycle
        with closing(self.app.get_connection()) as db:
            scope,_,_=life._sources(db,self.cid,self.file["id"])
        space=self.app.rag.embedding_space(self.file["config"],self.file["dimension"])
        life.store.delete_document_vectors(space,scope)
        return space,scope

    def authority(self):
        with closing(self.app.get_connection()) as db:
            return [dict(row) for row in db.execute("SELECT id,file_id,chunk_index,text,metadata,config FROM chunks ORDER BY id")]

    def test_missing_index_rebuild_and_restart_keep_citations_and_source_ids(self):
        self.send(use_files=True,file_ids=[self.file["id"]])
        before=self.authority()
        history=self.rows()
        self.remove_vectors()
        result=asyncio.run(self.recovery().rebuild_document(self.cid,self.file["id"]))
        self.assertEqual(result["status"],"rebuilt")
        self.assertGreater(result["embedding_calls"],0)
        self.assertEqual(self.authority(),before)
        self.app.init_db()
        self.assertEqual(self.rows(),history)
        self.assertEqual(self.migration.validate()["files"],1)
        with closing(self.app.get_connection()) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM vector_operations WHERE state='pending'").fetchone()[0],0)

    def test_healthy_generation_is_reused_without_inference(self):
        calls=self.embeds
        result=asyncio.run(self.recovery().rebuild_document(self.cid,self.file["id"]))
        self.assertEqual(result["status"],"reused")
        self.assertEqual(self.embeds,calls)

    def test_unavailable_digest_preserves_sources_and_does_not_embed(self):
        self.remove_vectors()
        before=self.authority();calls=self.embeds
        with patch.object(fixtures.existing.FakeModel,"digest","different-model"):
            with self.assertRaisesRegex(IndexUnavailable,"embedding space is unavailable"):
                asyncio.run(self.recovery().rebuild_document(self.cid,self.file["id"]))
        self.assertEqual(self.embeds,calls)
        self.assertEqual(self.authority(),before)
        with closing(self.app.get_connection()) as db:
            self.assertEqual(db.execute("SELECT state FROM vector_generations").fetchone()[0],"degraded")
        self.assertEqual(asyncio.run(self.recovery().rebuild_document(self.cid,self.file["id"]))["status"],"rebuilt")

    def test_changed_source_during_inference_is_not_published(self):
        self.remove_vectors()
        original=fixtures.existing.FakeModel.embed
        async def changed(client,*args,**kwargs):
            result=await original(client,*args,**kwargs)
            with closing(self.app.get_connection()) as db,db:
                db.execute("UPDATE chunks SET text=text || ' changed' WHERE file_id=?",(self.file["id"],))
            return result
        with patch.object(fixtures.existing.FakeModel,"embed",changed):
            with self.assertRaises(IndexInconsistent):
                asyncio.run(self.recovery().rebuild_document(self.cid,self.file["id"]))
        with self.assertRaises(ValueError):
            self.migration.validate()

    def test_cancellation_leaves_durable_retry_and_no_successful_publication(self):
        self.remove_vectors()
        async def cancelled(*args,**kwargs):
            with closing(self.app.get_connection()) as db:
                self.assertGreater(db.execute("SELECT COUNT(*) FROM vector_operations WHERE kind='upsert' AND state='pending'").fetchone()[0],0)
            raise asyncio.CancelledError()
        with patch.object(fixtures.existing.FakeModel,"embed",cancelled):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(self.recovery().rebuild_document(self.cid,self.file["id"]))
        self.app.init_db()
        self.assertEqual(asyncio.run(self.recovery().rebuild_document(self.cid,self.file["id"]))["status"],"rebuilt")

    def test_corrupted_numeric_vector_is_not_blessed_as_recovered(self):
        space,scope=self.remove_vectors()
        with closing(self.app.get_connection()) as db:
            _,_,rows=self.app.rag.lifecycle._sources(db,self.cid,self.file["id"])
        for row in rows:
            row["vector"]=[0.,1.,0.,0.]
        self.app.rag.vector_store.upsert_chunks(space,scope,rows)
        result=asyncio.run(self.recovery().rebuild_document(self.cid,self.file["id"]))
        self.assertEqual(result["status"],"rebuilt")
        self.assertGreater(result["embedding_calls"],0)

    def test_wrong_conversation_does_not_embed_or_expose_document(self):
        calls=self.embeds
        with self.assertRaises(IndexUnavailable):
            asyncio.run(self.recovery().rebuild_document(self.cid+100,self.file["id"]))
        self.assertEqual(self.embeds,calls)

    def test_finalized_database_rebuild_has_no_legacy_vector_writes(self):
        import time
        with closing(self.app.get_connection()) as db,db:
            db.execute("UPDATE vector_control SET activated_at=?",(time.time()-15*86400,))
        self.migration.finalize(decision=True)
        self.remove_vectors()
        result=asyncio.run(self.recovery().rebuild_document(self.cid,self.file["id"]))
        self.assertEqual(result["status"],"rebuilt")
        with closing(self.app.get_connection()) as db:
            self.assertNotIn("vector",{row["name"] for row in db.execute("PRAGMA table_info(chunks)")})


if __name__=="__main__":
    unittest.main()
