"""Small deterministic lexical compatibility study; no model or network access."""
from contextlib import closing
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from RAG.vector_store import EmbeddingSpace,GenerationScope,LanceVectorStore

TEXTS=[
    "Controller ZX-42 reports E_CONNRESET on port 8443.",
    "The red dragon protocol protects the handover.",
    "Pressure is 4.2 kPa with firmware version 3.14.",
    "The café manual describes Übergröße and façade tolerances.",
    "安全阀 pressure valve; давление контроль.",
    "Identifier aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa is reserved.",
    "An unrelated cooking instruction mentions red peppers and dragon fruit separately.",
]
QUERIES=['"ZX-42"','"E_CONNRESET"','"8443"','"red dragon"','"4.2"','"3.14"',
         '"café"','"cafe"','"Übergröße"','"安全阀"','"давление"',
         '"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"','"absent_identifier_009"']


def main():
    path=ROOT/"tests"/".artifacts"/("lx-"+uuid4().hex[:8])
    path.mkdir(parents=True)
    space=EmbeddingSpace("embeddinggemma:latest","lexical-fixture",3,"fixture-v1","","")
    scope=GenerationScope(1,"public",1,hashlib.sha256(b"public").hexdigest())
    private=replace(scope,conversation_id=2,file_id="private")
    store=LanceVectorStore(path/"v");store.initialize_index(space)
    records=[{"id":"SOURCE_"+str(i),"text":text,"metadata":{"line":i+1},"chunk_index":i,"vector":[1.,0.,0.]}
        for i,text in enumerate(TEXTS)]
    store.upsert_chunks(space,scope,records)
    store.upsert_chunks(space,private,records)
    store.build_text_index(space)
    with closing(sqlite3.connect(path/"lexical.db")) as db:
        db.execute("CREATE VIRTUAL TABLE fts USING fts5(id UNINDEXED,cid UNINDEXED,text)")
        db.executemany("INSERT INTO fts VALUES(?,?,?)",[(r["id"],cid,r["text"]) for cid in (1,2) for r in records])
        comparisons=[]
        for query in QUERIES:
            left=[r[0] for r in db.execute("SELECT id FROM fts WHERE fts MATCH ? AND cid=1 ORDER BY bm25(fts)",(query,))]
            right=[r["source_id"] for r in store.search_text(space,[scope],query)]
            comparisons.append({"query":query,"sqlite":left,"lance":right,"set_equivalent":set(left)==set(right)})
        scoped=store.search_text(space,[scope],'"red"',source_ids=["SOURCE_6"])
        assert all(r["source_id"]=="SOURCE_6" and r["conversation_id"]==1 for r in scoped)
        store.delete_document_vectors(space,private)
        assert not store.search_text(space,[private],'"red"')
    report={"scope":"Small lexical compatibility fixture; not broad language/search-quality validation",
        "queries":comparisons,"all_sets_equivalent":all(r["set_equivalent"] for r in comparisons),
        "scope_and_deletion_passed":True,"production_lexical_backend":"SQLite FTS5 retained"}
    output=ROOT/"tests"/"results"/"lexical-backend-comparison.json"
    output.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    print(json.dumps({"equivalent":sum(r["set_equivalent"] for r in comparisons),"queries":len(comparisons)}))


if __name__=="__main__":
    main()
