"""Publication and authority tests against populated isolated SQLite fixtures."""
from contextlib import closing
from dataclasses import replace
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from RAG.index_lifecycle import IndexLifecycle, EpochChanged
from RAG.storage_gate import StorageBusy
from RAG.vector_store import EmbeddingSpace, LanceVectorStore, IndexUnavailable, IndexInconsistent
from check_vector_store import SPACE, chunks


@unittest.skipUnless(importlib.util.find_spec("lancedb"),"Use the vector qualification environment")
class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory=ROOT/"tests"/".artifacts"/("lc-"+uuid4().hex[:10])
        self.directory.mkdir(parents=True)
        self.database=self.directory/"application.db"
        with closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE conversations(id INTEGER PRIMARY KEY);
                CREATE TABLE messages(id INTEGER PRIMARY KEY,content TEXT);
                CREATE TABLE files(id TEXT PRIMARY KEY,conversation_id INTEGER,sha256 TEXT,index_generation INTEGER,
                    original_filename TEXT);
                CREATE TABLE chunks(id TEXT PRIMARY KEY,file_id TEXT REFERENCES files(id) ON DELETE CASCADE,
                    chunk_index INTEGER,text TEXT,metadata TEXT,config TEXT,vector TEXT);
                CREATE TABLE citations(message_id INTEGER,chunk_id TEXT REFERENCES chunks(id) ON DELETE CASCADE);
                INSERT INTO conversations VALUES(1);
                INSERT INTO messages VALUES(1,'Historical answer [SOURCE_a]');
                CREATE TRIGGER advance_source AFTER UPDATE ON chunks BEGIN
                    UPDATE files SET index_generation=index_generation+1 WHERE id=NEW.file_id; END;
            """)
            db.execute("INSERT INTO files VALUES(?,?,?,?,?)",("file-a",1,hashlib.sha256(b"file-a").hexdigest(),5,"manual.txt"))
            for c in chunks():
                db.execute("INSERT INTO chunks VALUES(?,?,?,?,?,?,?)",
                    (c["id"],"file-a",c["chunk_index"],c["text"],json.dumps(c["metadata"]),"fixture",json.dumps(c["vector"])))
            db.execute("INSERT INTO citations VALUES(1,'SOURCE_a')")
        self.store=LanceVectorStore(self.directory/"v")
        self.store.initialize_index(SPACE)
        self.life=IndexLifecycle(self.connect,self.directory,self.store)
        self.life.init()

    def connect(self):
        db=sqlite3.connect(self.database,timeout=10)
        db.row_factory=sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def prepare(self,lease):
        op,scope,records=self.life.stage(lease,1,"file-a",SPACE)
        for c,original in zip(records,chunks()):
            c["vector"]=original["vector"]
        receipt=self.store.upsert_chunks(SPACE,scope,records)
        return op,scope,receipt

    def test_completed_operations_remove_unused_os_reservations(self):
        for _ in range(25):
            with self.life.operation("read") as lease:
                self.life.check(lease)
        self.assertEqual(list((self.directory/"coord"/"leases").glob("*.lock")),[])
        with closing(self.connect()) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM vector_leases").fetchone()[0],0)

    def test_publication_hydrates_sqlite_and_preserves_history(self):
        with self.life.operation("migration") as lease:
            op,scope,receipt=self.prepare(lease)
            with self.assertRaises(IndexUnavailable):
                self.life.search(lease,1,["file-a"],SPACE,[1.,0.,0.])
            self.life.publish(lease,op,SPACE,receipt)
            result=self.life.search(lease,1,["file-a"],SPACE,[1.,0.,0.])
            self.assertEqual(result[0]["text"],chunks()[0]["text"])
            self.assertNotIn("vector",result[0])
            with self.assertRaises(IndexUnavailable):
                self.life.search(lease,2,["file-a"],SPACE,[1.,0.,0.])
        with closing(self.connect()) as db, db:
            self.assertEqual(db.execute("SELECT content FROM messages").fetchone()[0],"Historical answer [SOURCE_a]")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM citations").fetchone()[0],1)

    def test_write_acknowledgment_loss_restart_is_recoverable(self):
        with self.life.operation("index") as lease:
            op,scope,receipt=self.prepare(lease)
        # Simulated crash boundary: vectors durable, SQLite still pending.
        restarted=IndexLifecycle(self.connect,self.directory,LanceVectorStore(self.directory/"v"))
        with restarted.operation("recover") as lease:
            self.assertEqual(restarted.stage(lease,1,"file-a",SPACE)[0],op)
            restarted.publish(lease,op,SPACE,receipt)
            restarted.publish(lease,op,SPACE,receipt)
            self.assertEqual(len(restarted.search(lease,1,["file-a"],SPACE,[1.,0.,0.])),2)
        self.assertEqual(self.store.get_index_status(SPACE)["rows"],2)

    def test_metadata_failure_does_not_publish(self):
        with self.life.operation("index") as lease:
            op,scope,receipt=self.prepare(lease)
            with closing(self.connect()) as db, db:
                db.execute("""CREATE TRIGGER fail_metadata BEFORE UPDATE ON vector_operations
                    WHEN NEW.state='complete' BEGIN SELECT RAISE(ABORT,'injected failure'); END""")
            with self.assertRaises(sqlite3.IntegrityError):
                self.life.publish(lease,op,SPACE,receipt)
            with closing(self.connect()) as db, db:
                self.assertEqual(db.execute("SELECT state FROM vector_generations").fetchone()[0],"pending")
                db.execute("DROP TRIGGER fail_metadata")
            self.life.publish(lease,op,SPACE,receipt)

    def test_replacement_between_write_and_publication_fails(self):
        with self.life.operation("index") as lease:
            op,scope,receipt=self.prepare(lease)
            with closing(self.connect()) as db, db:
                db.execute("UPDATE files SET index_generation=index_generation+1,sha256=?",("c"*64,))
            with self.assertRaises(IndexInconsistent):
                self.life.publish(lease,op,SPACE,receipt)

    def test_delete_creates_outbox_and_prevents_old_candidate_hydration(self):
        with self.life.operation("index") as lease:
            op,scope,receipt=self.prepare(lease)
            self.life.publish(lease,op,SPACE,receipt)
            snapshots=self.life.snapshot(lease,1,["file-a"],SPACE)
            candidates=self.store.search_vectors(SPACE,[scope],[1.,0.,0.])
            with closing(self.connect()) as db, db:
                db.execute("DELETE FROM files")
            with self.assertRaises(IndexUnavailable):
                self.life.hydrate(lease,SPACE,snapshots,candidates)
            with closing(self.connect()) as db, db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM vector_operations WHERE kind='delete'").fetchone()[0],1)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM citations").fetchone()[0],0)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0],1)

    def test_corruption_reports_degraded_not_absent_answer(self):
        with self.life.operation("index") as lease:
            op,scope,receipt=self.prepare(lease)
            self.life.publish(lease,op,SPACE,receipt)
            self.store.delete_source_vectors(SPACE,scope,["SOURCE_b"])
            with self.assertRaises(IndexInconsistent):
                self.life.search(lease,1,["file-a"],SPACE,[1.,0.,0.])

    def test_epoch_transition_drains_active_leases_even_if_expired(self):
        with self.life.operation("legacy") as lease:
            with closing(self.connect()) as db, db:
                db.execute("UPDATE vector_leases SET expires_at=0")
            with self.assertRaises(StorageBusy):
                self.life._set_backend(0,"lancedb","CUTOVER_ACTIVE",{})
            with self.assertRaises(EpochChanged):
                self.life.check(lease)
        self.life._set_backend(0,"lancedb","CUTOVER_ACTIVE",{})
        self.assertEqual(self.life.control()["epoch"],1)
        with self.assertRaises(EpochChanged):
            self.life.check(lease)
        self.life._set_backend(1,"sqlite","PRE_ACTIVATION",{})
        self.assertEqual(self.life.control()["epoch"],2)

    def test_generation_changes_in_same_sqlite_transaction(self):
        with self.life.operation("index") as lease:
            op,scope,receipt=self.prepare(lease)
            self.life.publish(lease,op,SPACE,receipt)
            with closing(self.connect()) as db, db:
                db.execute("UPDATE chunks SET text='changed' WHERE id='SOURCE_a'")
                self.assertEqual(db.execute("SELECT state FROM vector_generations").fetchone()[0],"stale")
                db.rollback()
            self.assertEqual(len(self.life.search(lease,1,["file-a"],SPACE,[1.,0.,0.])),2)

    def test_reconciliation_recovers_acknowledgment_loss_without_embeddings(self):
        with self.life.operation("index") as lease:
            op,scope,receipt=self.prepare(lease)
        self.assertEqual(self.life.reconcile()["recovered"],1)
        with self.life.operation("read") as lease:
            self.assertEqual(len(self.life.search(lease,1,["file-a"],SPACE,[1.,0.,0.])),2)

    def test_delete_outbox_reclaims_current_rows_idempotently(self):
        with self.life.operation("index") as lease:
            op,scope,receipt=self.prepare(lease)
            self.life.publish(lease,op,SPACE,receipt)
        with closing(self.connect()) as db,db:
            db.execute("DELETE FROM files")
        self.assertEqual(self.life.reconcile()["deleted"],1)
        self.assertEqual(self.store.get_index_status(SPACE)["rows"],0)
        self.assertEqual(self.life.reconcile()["deleted"],0)

    def test_late_native_write_after_delete_ack_is_reclaimed(self):
        with self.life.operation("index") as lease:
            op,scope,receipt=self.prepare(lease)
            with closing(self.connect()) as db,db:
                db.execute("DELETE FROM files")
            # Delete wins first. A delayed native write then recreates old rows.
            self.life.reconcile()
            self.life.reconcile()
            records=chunks()
            self.store.upsert_chunks(SPACE,scope,records)
            self.assertGreater(self.store.get_index_status(SPACE)["rows"],0)
            with self.assertRaises(IndexUnavailable):
                self.life.publish(lease,op,SPACE,receipt)
        # Reconciliation recognizes obsolete pending work and re-arms deletion.
        self.life.reconcile()
        self.life.reconcile()
        self.assertEqual(self.store.get_index_status(SPACE)["rows"],0)

    def test_1000_publication_compare_and_swap_interleavings(self):
        # Deterministic SQLite publication boundary stress. Native visibility and
        # multiple-process writes have separate adapter tests.
        with self.life.operation("stress",ttl=1800) as lease:
            for i in range(1000):
                op,scope,records=self.life.stage(lease,1,"file-a",SPACE)
                from RAG.vector_store import row_identity,source_fingerprint
                manifest={row_identity(SPACE,scope,c["id"]):{"source_id":c["id"],
                    "source_fingerprint":source_fingerprint(c["text"],c["metadata"]),
                    "vector_fingerprint":"fixture"} for c in records}
                def change(*args):
                    with closing(self.connect()) as db, db:
                        db.execute("UPDATE files SET index_generation=index_generation+1 WHERE id='file-a'")
                with patch.object(self.store,"verify_index_consistency",side_effect=change):
                    with self.assertRaises(IndexInconsistent):
                        self.life.publish(lease,op,SPACE,{"manifest":manifest,"version":i+1})
            with closing(self.connect()) as db, db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM vector_generations WHERE state='ready'").fetchone()[0],0)


if __name__=="__main__":
    unittest.main()
