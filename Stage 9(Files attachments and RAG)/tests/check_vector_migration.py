"""Populated migration fixtures; never activate or clean up the live database."""
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import check_index_lifecycle as fixtures
from check_vector_store import SPACE
from RAG.vector_migration import VectorMigration, MigrationError

# Fixture-only attestation exercises state transitions, not real qualification.
ACCEPTANCE={"frozen_version":1,"checks":{name:True for name in
    ("runtime","regression","populated_migration","authorization","citations","recovery",
     "publication","retrieval_quality","performance","offline_worker_rollout")},
    "artifacts":["fixture-only: not production acceptance"]}


@unittest.skipUnless(importlib.util.find_spec("lancedb"),"Use the vector qualification environment")
class MigrationTests(unittest.TestCase):
    connect=fixtures.LifecycleTests.connect

    def setUp(self):
        fixtures.LifecycleTests.setUp(self)
        with closing(self.connect()) as db,db:
            db.execute("ALTER TABLE files ADD COLUMN config TEXT")
            db.execute("UPDATE files SET config='fixture'")
        self.migration=VectorMigration(self.life,self.directory.parent/("backup-"+uuid4().hex[:8]))

    def prepared(self):
        result=self.migration.prepare(lambda file:SPACE)
        self.assertEqual(result,{"files":1,"sources":2,"vectors":2})

    def activate(self):
        self.prepared()
        self.migration.activate(ACCEPTANCE)

    def test_preparation_retries_preserve_authoritative_records(self):
        self.prepared();self.prepared()
        self.assertEqual(self.life.control()["backend"],"sqlite")
        self.assertEqual(self.store.get_index_status(SPACE)["rows"],2)
        with closing(self.connect()) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM citations").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT text FROM chunks WHERE id='SOURCE_a'").fetchone()[0],
                "ZX-42 battery pressure is 4.2 kPa.")

    def test_cutover_requires_complete_acceptance_and_verified_backup(self):
        self.prepared()
        with self.assertRaises(MigrationError):
            self.migration.activate({"checks":{"runtime":True}})
        record=json.loads(self.life.control()["manifest"])["backup"]
        Path(record["path"]).write_bytes(b"corrupt fixture backup")
        with self.assertRaises(MigrationError):
            self.migration.activate(ACCEPTANCE)
        self.assertEqual(self.life.control()["backend"],"sqlite")

    def test_rollback_preserves_post_cutover_messages(self):
        self.activate()
        with closing(self.connect()) as db,db:
            db.execute("INSERT INTO messages VALUES(2,'New chat after cutover')")
        with self.assertRaises(MigrationError):
            self.migration.rollback()
        self.migration.rollback(decision=True)
        with closing(self.connect()) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0],2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM citations").fetchone()[0],1)
        self.assertEqual(self.life.control()["backend"],"sqlite")
        self.assertEqual(self.life.control()["epoch"],2)

    def test_finalization_time_decision_and_schema_guards(self):
        self.activate()
        with self.assertRaises(MigrationError):
            self.migration.finalize()
        with self.assertRaises(MigrationError):
            self.migration.finalize(decision=True)
        # Time is advanced only in this isolated fixture; no live period is waived.
        with closing(self.connect()) as db,db:
            db.execute("UPDATE vector_control SET activated_at=?",(time.time()-15*86400,))
        result=self.migration.finalize(decision=True)
        self.assertEqual(result["lifecycle"],"FINALIZED")
        with closing(self.connect()) as db:
            self.assertNotIn("vector",{r["name"] for r in db.execute("PRAGMA table_info(chunks)")})
            self.assertEqual(db.execute("SELECT COUNT(*) FROM citations").fetchone()[0],1)
            self.assertFalse(db.execute("PRAGMA foreign_key_check").fetchall())
        with self.assertRaises(MigrationError):
            self.migration.rollback(decision=True)

    def finalized(self):
        self.activate()
        with closing(self.connect()) as db,db:
            db.execute("UPDATE vector_control SET activated_at=?",(time.time()-15*86400,))
        self.migration.finalize(decision=True)

    def age_backups(self):
        with closing(self.connect()) as db,db:
            manifest=json.loads(self.life.control(db)["manifest"])
            for key in ("backup","rollback_backup","finalization_backup"):
                if key in manifest:
                    manifest[key]["created_at"]=time.time()-15*86400
            db.execute("UPDATE vector_control SET manifest=?",(json.dumps(manifest),))
        return manifest

    def test_retirement_requires_decision_and_minimum_snapshot_retention(self):
        self.finalized()
        with self.assertRaises(MigrationError):
            self.migration.retire()
        with self.assertRaisesRegex(MigrationError,"14 days"):
            self.migration.retire(decision=True)
        self.assertEqual(self.life.control()["lifecycle"],"FINALIZED")

    def test_retirement_keeps_new_vector_free_recovery_snapshot(self):
        self.finalized()
        manifest=self.age_backups()
        result=self.migration.retire(decision=True)
        self.assertEqual(result["lifecycle"],"RETIRED")
        self.assertFalse(Path(manifest["backup"]["path"]).exists())
        self.assertFalse(Path(manifest["finalization_backup"]["path"]).exists())
        recovery=result["recovery_backup"]
        self.migration.verify_backup(recovery)
        self.assertEqual(recovery["backend"],"lancedb")
        self.assertEqual(recovery["generations"],1)
        self.assertTrue(recovery["generation_manifest_digest"])
        with closing(sqlite3.connect(recovery["path"])) as db:
            self.assertNotIn("vector",{row[1] for row in db.execute("PRAGMA table_info(chunks)")})
            self.assertEqual(db.execute("SELECT COUNT(*) FROM citations").fetchone()[0],1)

    def test_retirement_recovers_unlink_acknowledgment_loss(self):
        self.finalized()
        manifest=self.age_backups()
        original=self.migration._record
        failed=False
        def crash_after_unlink(**changes):
            nonlocal failed
            if changes.get("retirement",{}).get("removed") and not failed:
                failed=True
                raise RuntimeError("simulated crash after unlink")
            return original(**changes)
        with patch.object(self.migration,"_record",crash_after_unlink):
            with self.assertRaises(RuntimeError):
                self.migration.retire(decision=True)
        self.assertEqual(self.life.control()["lifecycle"],"FINALIZED")
        self.assertFalse(Path(manifest["backup"]["path"]).exists())
        self.assertEqual(self.migration.retire(decision=True)["lifecycle"],"RETIRED")

    def test_retirement_rejects_an_outside_backup_path(self):
        self.finalized()
        manifest=self.age_backups()
        outside=self.directory/"untouched.db"
        outside.write_bytes(Path(manifest["backup"]["path"]).read_bytes())
        manifest["backup"]["path"]=str(outside)
        with closing(self.connect()) as db,db:
            db.execute("UPDATE vector_control SET manifest=?",(json.dumps(manifest),))
        with self.assertRaises(MigrationError):
            self.migration.retire(decision=True)
        self.assertTrue(outside.exists())

    def test_unknown_vector_dependency_prevents_destructive_cleanup(self):
        self.activate()
        with closing(self.connect()) as db,db:
            db.execute("UPDATE vector_control SET activated_at=?",(time.time()-15*86400,))
            db.execute("CREATE VIEW unknown_vector_view AS SELECT vector FROM chunks")
        with self.assertRaises(MigrationError):
            self.migration.finalize(decision=True)
        self.assertEqual(self.life.control()["lifecycle"],"CUTOVER_ACTIVE")
        with closing(self.connect()) as db:
            self.assertIn("vector",{r["name"] for r in db.execute("PRAGMA table_info(chunks)")})

    def test_mutated_sources_prevent_cutover_and_do_not_destroy_legacy(self):
        self.prepared()
        with closing(self.connect()) as db,db:
            db.execute("UPDATE chunks SET text='replacement' WHERE id='SOURCE_a'")
        with self.assertRaises(MigrationError):
            self.migration.activate(ACCEPTANCE)
        with closing(self.connect()) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],2)
        self.assertEqual(self.life.control()["backend"],"sqlite")


if __name__=="__main__":
    unittest.main()
