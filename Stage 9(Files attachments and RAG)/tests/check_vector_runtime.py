"""Existing chat/SSE contract through a fixture-only dedicated-backend cutover."""
from contextlib import closing
import importlib.util
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import check_rag as existing
from check_vector_migration import ACCEPTANCE
from RAG.vector_migration import VectorMigration


@unittest.skipUnless(importlib.util.find_spec("lancedb"),"Install requirements-vector-dev.txt")
class RuntimeTests(unittest.TestCase):
    upload=existing.Checks.upload
    send=existing.Checks.send
    rows=existing.Checks.rows

    def setUp(self):
        self.vector_dir=ROOT/"tests"/".artifacts"/("vr-"+uuid4().hex[:8])
        self.vector_setting=patch.dict(os.environ,{"VECTOR_DB_DIR":str(self.vector_dir)})
        self.vector_setting.start()
        existing.Checks.setUp(self)
        self.migration=VectorMigration(self.app.rag.lifecycle,ROOT/"tests"/".artifacts"/("br-"+uuid4().hex[:8]))
        self.file=self.upload()
        self.migration.prepare(lambda file:self.app.rag.embedding_space(file["config"],file["dimension"]))
        self.migration.activate(ACCEPTANCE)

    def tearDown(self):
        existing.Checks.tearDown(self)
        self.vector_setting.stop()

    def test_chat_after_cutover_never_calls_legacy_dense_scan(self):
        with patch.object(self.app.rag,"retrieve_dense",side_effect=AssertionError("Legacy scan called")):
            events=self.send(attachment_ids=[self.file["id"]],use_files=True,file_ids=[self.file["id"]])
        self.assertEqual(events[-1]["status"],"completed",events)
        reply=self.rows()[-1]
        self.assertIn("Blue Falcon",reply["content"])
        self.assertTrue(reply["sources"])
        source=reply["sources"][0]["id"]
        preview=self.client.get(f"/conversations/{self.cid}/sources/{source}")
        self.assertEqual(preview.status_code,200)
        self.assertIn("Blue Falcon",preview.json()["text"])

    def test_new_embeddings_only_in_vector_store(self):
        second=self.upload("other.txt",b"The second project codename is Green Falcon.")
        with closing(self.app.get_connection()) as db:
            values=[r[0] for r in db.execute("SELECT vector FROM chunks WHERE file_id=?",(second["id"],))]
            self.assertTrue(values)
            self.assertEqual(set(values),{"[]"})
        with closing(self.app.get_connection()) as db,db:
            with self.assertRaises(Exception):
                db.execute("UPDATE chunks SET vector='[1,0,0,0]' WHERE file_id=?",(second["id"],))
        result=self.send(use_files=True,file_ids=[second["id"]])
        self.assertEqual(result[-1]["status"],"completed",result)

    def test_upload_commits_sources_and_pending_intent_before_embedding(self):
        original=existing.FakeModel.embed
        observed=[]
        async def inspect(client,*args,**kwargs):
            with closing(self.app.get_connection()) as db:
                job=db.execute("SELECT * FROM vector_builds WHERE state='pending'").fetchone()
                self.assertIsNotNone(job)
                self.assertGreater(db.execute("SELECT COUNT(*) FROM chunks WHERE file_id=?",(job["file_id"],)).fetchone()[0],0)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM chunks WHERE file_id=? AND vector!='[]'",(job["file_id"],)).fetchone()[0],0)
                observed.append(job["id"])
            return await original(client,*args,**kwargs)
        with patch.object(existing.FakeModel,"embed",inspect):
            second=self.upload("durable.txt",b"Durable project codename is Amber Falcon.")
        self.assertTrue(observed)
        with closing(self.app.get_connection()) as db:
            self.assertEqual(db.execute("SELECT state FROM vector_builds WHERE file_id=?",(second["id"],)).fetchone()[0],"complete")

    def test_interrupted_upload_keeps_retryable_intent_and_parsed_content(self):
        import asyncio
        async def fail(*args,**kwargs):
            raise RuntimeError("injected embedding failure")
        with patch.object(existing.FakeModel,"embed",fail):
            second=self.upload("interrupted.txt",b"Recoverable project codename is Amber Falcon.",ready=False)
            for _ in range(500):
                if self.app.documents.row(self.cid,second["id"])["state"]=="failed":
                    break
                time.sleep(.01)
            self.assertEqual(self.app.documents.row(self.cid,second["id"])["state"],"failed")
        with closing(self.app.get_connection()) as db:
            self.assertEqual(db.execute("SELECT state FROM vector_builds WHERE file_id=?",(second["id"],)).fetchone()[0],"pending")
            self.assertGreater(db.execute("SELECT COUNT(*) FROM chunks WHERE file_id=?",(second["id"],)).fetchone()[0],0)
        self.app.init_db()
        asyncio.run(self.app.rag.index(self.cid,second["id"]))
        self.assertEqual(self.app.documents.row(self.cid,second["id"])["state"],"ready")
        self.assertEqual(self.migration.validate()["files"],2)

    def test_live_worker_observes_rollback_epoch_and_current_history(self):
        self.send(use_files=False)
        self.migration.rollback(decision=True)
        self.assertEqual(self.app.rag.lifecycle.control()["backend"],"sqlite")
        result=self.send(use_files=True,file_ids=[self.file["id"]])
        self.assertEqual(result[-1]["status"],"completed",result)
        self.assertEqual(len(self.rows()),4)

    def test_finalized_schema_survives_restart_and_new_indexing(self):
        with closing(self.app.get_connection()) as db,db:
            db.execute("UPDATE vector_control SET activated_at=?",(time.time()-15*86400,))
        self.migration.finalize(decision=True)
        self.app.init_db()
        with closing(self.app.get_connection()) as db:
            self.assertNotIn("vector",{r["name"] for r in db.execute("PRAGMA table_info(chunks)")})
        second=self.upload("later.txt",b"The project codename is Final Falcon.")
        result=self.send(use_files=True,file_ids=[second["id"]])
        self.assertEqual(result[-1]["status"],"completed",result)

    def test_missing_index_produces_explicit_error_not_no_evidence_answer(self):
        self.app.rag.vector_store.shutdown()
        result=self.send(use_files=True,file_ids=[self.file["id"]])
        self.assertNotEqual(result[-1]["status"],"completed",result)
        self.assertTrue(any(e["type"]=="error" for e in result),result)


if __name__=="__main__":
    unittest.main()
