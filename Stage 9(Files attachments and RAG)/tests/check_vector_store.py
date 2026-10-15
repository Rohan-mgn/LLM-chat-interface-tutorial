"""Native adapter tests. Run with the isolated, pinned vector qualification Python."""
from dataclasses import replace
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from RAG.vector_store import (EmbeddingSpace, GenerationScope, LanceVectorStore,
    IndexInconsistent, IndexUnavailable, normalized, scope_filter)
from RAG.storage_gate import ProcessGate, StorageBusy

SPACE = EmbeddingSpace("embeddinggemma:latest", "fixture-digest", 3, "fixture-v1", "document:", "query:")
SCOPE = GenerationScope(1, "file-a", 5, hashlib.sha256(b"file-a").hexdigest())


def chunks():
    return [{"id": "SOURCE_a", "text": "ZX-42 battery pressure is 4.2 kPa.", "metadata": {"page": 1},
             "chunk_index": 0, "vector": [1., 0., 0.]},
            {"id": "SOURCE_b", "text": "A red dragon sleeps.", "metadata": {"page": 2},
             "chunk_index": 1, "vector": [0., 1., 0.]}]


def worker(path, action):
    store = LanceVectorStore(path)
    if action == "write":
        for i in range(10):
            store.upsert_chunks(SPACE, SCOPE, chunks())
    elif action == "crash":
        store.upsert_chunks(SPACE, SCOPE, chunks())
        os._exit(7)
    elif action == "advance":
        for i in range(1,121):
            scope=replace(SCOPE,generation=100+i)
            data=chunks()
            data[0]["text"]="Published generation "+str(i)
            store.upsert_chunks(SPACE,scope,data)
    elif action == "gate":
        with ProcessGate(Path(path)/"test.lock", timeout=.2).hold():
            raise AssertionError("Other process acquired an owned gate")
    elif action == "read":
        for _ in range(15):
            assert store.search_vectors(SPACE, [SCOPE], [1., 0., 0.])[0]["source_id"] == "SOURCE_a"
    return 0


@unittest.skipUnless(importlib.util.find_spec("lancedb"), "Use the vector qualification environment")
class VectorStoreTests(unittest.TestCase):
    def setUp(self):
        self.path = ROOT/"tests"/".artifacts"/("vs-"+uuid4().hex[:12])
        self.store = LanceVectorStore(self.path)
        self.store.initialize_index(SPACE)
        self.receipt = self.store.upsert_chunks(SPACE, SCOPE, chunks())

    def child(self, action):
        return subprocess.Popen([sys.executable, "-B", __file__, "--child", str(self.path), action],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_supplied_cosine_identity_only_and_authorized_prefilter(self):
        private = replace(SCOPE, conversation_id=2, file_id="private")
        self.store.upsert_chunks(SPACE, private, chunks())
        result = self.store.search_vectors(SPACE, [SCOPE], [1., 0., 0.], 1)
        self.assertEqual(result[0]["source_id"], "SOURCE_a")
        self.assertEqual(result[0]["conversation_id"], 1)
        self.assertNotIn("text", result[0])
        self.assertNotIn("vector", result[0])
        self.assertAlmostEqual(result[0]["score"], 1., places=6)
        result = self.store.search_vectors(SPACE, [SCOPE], [1., 0., 0.], 1, ["SOURCE_b"])
        self.assertEqual(result[0]["source_id"], "SOURCE_b")

    def test_multibatch_merge_is_atomic_and_retry_does_not_duplicate(self):
        scope=replace(SCOPE,generation=91)
        records=[{"id":"SOURCE_batch_"+str(i),"text":"Batch record "+str(i),"metadata":{"line":i+1},
                  "chunk_index":i,"vector":[1.,0.,0.]} for i in range(260)]
        invalid=[dict(row) for row in records]
        invalid[-1]["vector"]=[0.,0.,0.]
        with self.assertRaises(Exception):
            self.store.upsert_chunks(SPACE,scope,invalid)
        self.assertEqual(self.store.search_vectors(SPACE,[scope],[1.,0.,0.]),[])
        receipt=self.store.upsert_chunks(SPACE,scope,records)
        repeated=self.store.upsert_chunks(SPACE,scope,records)
        self.assertEqual(receipt["manifest"],repeated["manifest"])
        self.assertEqual(self.store.verify_index_consistency(SPACE,scope,receipt["manifest"])["count"],260)
        self.assertEqual(self.store.get_index_status(SPACE)["rows"],262)

    def test_idempotent_retry_and_restart(self):
        for _ in range(100):
            self.store.upsert_chunks(SPACE, SCOPE, chunks())
        self.assertEqual(self.store.get_index_status(SPACE)["rows"], 2)
        child = self.child("crash")
        _, error = child.communicate(timeout=30)
        self.assertEqual(child.returncode, 7, error)
        reopened = LanceVectorStore(self.path)
        reopened.upsert_chunks(SPACE, SCOPE, chunks())
        self.assertEqual(reopened.get_index_status(SPACE)["rows"], 2)

    def test_concurrent_process_readers_writers(self):
        children = [self.child("write"), self.child("read"), self.child("write")]
        for child in children:
            _, error = child.communicate(timeout=90)
            self.assertEqual(child.returncode, 0, error)
        self.assertEqual(self.store.verify_index_consistency(SPACE, SCOPE, self.receipt["manifest"])["count"], 2)

    def test_reader_observes_new_generations_from_another_process(self):
        child=self.child("advance")
        _,error=child.communicate(timeout=120)
        self.assertEqual(child.returncode,0,error)
        # Reopen the same long-lived adapter after 120 actual generation writes.
        for i in range(1,121):
            scope=replace(SCOPE,generation=100+i)
            rows=self.store.search_vectors(SPACE,[scope],[1.,0.,0.],1)
            self.assertEqual(rows[0]["generation"],100+i)
            self.assertEqual(rows[0]["source_id"],"SOURCE_a")
        self.assertEqual(self.store.get_index_status(SPACE)["rows"],242)

    def test_cross_process_gate_and_nested_thread_gate(self):
        gate = ProcessGate(self.path/"test.lock")
        with gate.hold(), ProcessGate(self.path/"test.lock").hold():
            child = self.child("gate")
            _, error = child.communicate(timeout=30)
            self.assertNotEqual(child.returncode, 0)
            self.assertIn("StorageBusy", error)
        with gate.hold():
            pass

    def test_model_generation_and_fingerprint_filters(self):
        self.assertEqual(self.store.search_vectors(SPACE, [replace(SCOPE, generation=6)], [1., 0., 0.]), [])
        self.assertEqual(self.store.search_vectors(SPACE, [replace(SCOPE, file_fingerprint="b"*64)], [1., 0., 0.]), [])
        with self.assertRaises(IndexUnavailable):
            self.store.search_vectors(replace(SPACE, model_digest="changed"), [SCOPE], [1., 0., 0.])
        with self.assertRaises(ValueError):
            scope_filter(SPACE, [SCOPE, replace(SCOPE, conversation_id=2)])
        with self.assertRaises(ValueError):
            scope_filter(SPACE, [SCOPE], ["x' OR 1=1"])

    def test_malformed_vectors_and_duplicate_sources(self):
        for value in ([0,0,0], [1,float("nan"),0], [1,float("inf"),0], [1,0], [True,0,0]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalized(value, 3)
        with self.assertRaises(ValueError):
            self.store.upsert_chunks(SPACE, SCOPE, [chunks()[0], chunks()[0]])
        with self.assertRaises(ValueError):
            self.store.search_vectors(SPACE, [SCOPE], [1.,0.,0.], 1000)

    def test_manifest_detects_missing_rows_even_at_newer_version(self):
        self.store.delete_source_vectors(SPACE, SCOPE, ["SOURCE_b"])
        self.assertGreater(self.store.get_index_status(SPACE)["version"], self.receipt["version"])
        with self.assertRaises(IndexInconsistent):
            self.store.verify_index_consistency(SPACE, SCOPE, self.receipt["manifest"])
        self.store.upsert_chunks(SPACE, SCOPE, chunks())
        self.store.verify_index_consistency(SPACE, SCOPE, self.receipt["manifest"])

    def test_fts_incremental_deletion_phrase_and_scope(self):
        self.store.build_text_index(SPACE)
        self.assertEqual(self.store.search_text(SPACE, [SCOPE], '"red dragon"')[0]["source_id"], "SOURCE_b")
        new = {"id":"SOURCE_c","text":"RARE_IDENTIFIER_772 critical fault", "metadata":{}, "chunk_index":2,"vector":[0.,0.,1.]}
        # Whole-generation retries retain the exact manifest.
        self.store.upsert_chunks(SPACE, SCOPE, [*chunks(),new])
        self.assertEqual(self.store.search_text(SPACE, [SCOPE], "critical")[0]["source_id"], "SOURCE_c")
        self.store.delete_source_vectors(SPACE, SCOPE, ["SOURCE_c"])
        self.assertFalse(self.store.search_text(SPACE, [SCOPE], "critical"))
        self.assertFalse(self.store.search_text(SPACE, [replace(SCOPE,conversation_id=2)], "dragon"))

    def test_replacement_preserves_old_identity_without_returning_it(self):
        newer = replace(SCOPE, generation=6, file_fingerprint="c"*64)
        receipt = self.store.upsert_chunks(SPACE, newer, chunks())
        self.assertTrue(set(receipt["manifest"]).isdisjoint(self.receipt["manifest"]))
        result = self.store.search_vectors(SPACE,[newer],[1.,0.,0.])
        self.assertTrue(all(r["generation"] == 6 for r in result))
        self.store.delete_document_vectors(SPACE,SCOPE)
        self.assertEqual(self.store.get_index_status(SPACE)["rows"],2)

    def test_maintenance_retains_protected_versions_shutdown_and_paths(self):
        with self.assertRaises(ValueError):
            self.store.maintenance(SPACE,allow_cleanup=True,protected_versions=[self.receipt["version"]])
        self.store.maintenance(SPACE)
        self.store.verify_index_consistency(SPACE,SCOPE,self.receipt["manifest"])
        self.store.shutdown()
        with self.assertRaises(IndexUnavailable):
            self.store.get_index_status(SPACE)
        with self.assertRaises(ValueError):
            LanceVectorStore("relative/path")


if __name__ == "__main__":
    if "--child" in sys.argv:
        sys.exit(worker(sys.argv[-2],sys.argv[-1]))
    unittest.main()
