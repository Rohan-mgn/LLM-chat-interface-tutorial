"""A failed initialization batch must not publish half a schema."""
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import shutil
from uuid import uuid4
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from RAG.documents import Documents
from RAG.index_lifecycle import IndexLifecycle


class SchemaInitializationTests(unittest.TestCase):
    def setUp(self):
        base=Path(__file__).resolve().parent/".artifacts"
        base.mkdir(exist_ok=True)
        self.directory=base/("schema-"+uuid4().hex)
        self.directory.mkdir()
        self.database=self.directory/"chat.db"
        self.denied=None
        with closing(self.connect()) as db,db:
            db.executescript("""BEGIN;
                CREATE TABLE conversations(id INTEGER PRIMARY KEY);
                CREATE TABLE messages(id INTEGER PRIMARY KEY,conversation_id INTEGER,content TEXT);
                INSERT INTO conversations VALUES(1);
                INSERT INTO messages VALUES(1,1,'Keep this history');
            """)

    def tearDown(self):
        assert self.directory.resolve().parent==(Path(__file__).resolve().parent/".artifacts").resolve()
        assert self.directory.name.startswith("schema-")
        shutil.rmtree(self.directory)

    def connect(self):
        db=sqlite3.connect(self.database)
        db.row_factory=sqlite3.Row
        if self.denied:
            db.set_authorizer(lambda action,name,*_:sqlite3.SQLITE_DENY if (action,name)==self.denied else sqlite3.SQLITE_OK)
        return db

    def names(self):
        with closing(self.connect()) as db:
            self.assertEqual(db.execute("SELECT content FROM messages").fetchone()[0],"Keep this history")
            return {r[0] for r in db.execute("SELECT name FROM sqlite_master")}

    def test_document_batch_rolls_back_and_retries(self):
        docs=Documents(self.connect,lambda:self.directory)
        self.denied=(sqlite3.SQLITE_CREATE_TABLE,"rag_runs")
        with self.assertRaises(sqlite3.DatabaseError):docs.init()
        self.assertNotIn("files",self.names())
        self.assertNotIn("chunks",self.names())
        self.denied=None;docs.init()
        self.assertIn("rag_runs",self.names())

    def test_vector_batch_including_write_guards_rolls_back(self):
        Documents(self.connect,lambda:self.directory).init()
        life=IndexLifecycle(self.connect,self.directory,None)
        self.denied=(sqlite3.SQLITE_CREATE_TRIGGER,"vector_write_guard_insert")
        with self.assertRaises(sqlite3.DatabaseError):life.init()
        self.assertNotIn("vector_control",self.names())
        self.assertNotIn("vector_delete_outbox",self.names())
        self.denied=None;life.init()
        self.assertEqual(life.control()["backend"],"sqlite")
        self.assertIn("vector_write_guard_insert",self.names())

    def test_optional_fts_failure_does_not_leave_partial_triggers(self):
        docs=Documents(self.connect,lambda:self.directory)
        self.denied=(sqlite3.SQLITE_CREATE_TRIGGER,"chunks_fts_delete")
        with self.assertRaises(sqlite3.DatabaseError):docs.init()
        self.assertFalse(docs.fts_available)
        self.assertNotIn("chunks_fts",self.names())
        self.assertNotIn("chunks_fts_insert",self.names())
        self.denied=None;docs.upgrade()
        self.assertTrue(docs.fts_available)
        self.assertIn("chunks_fts_delete",self.names())


if __name__=="__main__":unittest.main()
