"""Isolated native runtime qualification. Never opens application data or Ollama."""
import argparse
from datetime import timedelta
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
def connect(path):
    import lancedb
    return lancedb.connect(str(path), read_consistency_interval=timedelta(0))

def row(key, cid, vector, text):
    return dict(row_id=key, conversation_id=cid, file_id="file-"+str(cid), generation=1,
                space="fixture-space", fingerprint=key, text=text, vector=vector)

def upsert(table, rows):
    table.merge_insert("row_id").when_matched_update_all().when_not_matched_insert_all().execute(rows)

def child(path, action):
    table=connect(path).open_table("chunks")
    if action=="read":
        assert table.count_rows("conversation_id = 1")==2
        print("reopened")
    else:
        upsert(table,[row("c",1,[0.,1.,0.],"written by another process")])
        if action=="crash":os._exit(7)
    return 0

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--child",nargs=2)
    parser.add_argument("--output",type=Path,default=ROOT/"results"/"lancedb-qualification.json")
    args=parser.parse_args()
    if args.child:return child(*args.child)
    started=time.perf_counter()
    path=ROOT/".artifacts"/("lance-qualification-"+uuid4().hex)
    path.mkdir(parents=True)
    import lancedb
    import pyarrow as pa
    checks=[]
    def checked(name, fn):
        begin=time.perf_counter()
        fn()
        checks.append({"name":name,"passed":True,"seconds":round(time.perf_counter()-begin,3)})
        print(name+": passed",flush=True)
    db=connect(path)
    schema=pa.schema([pa.field("row_id",pa.string()),pa.field("conversation_id",pa.int64()),
        pa.field("file_id",pa.string()),pa.field("generation",pa.int64()),pa.field("space",pa.string()),
        pa.field("fingerprint",pa.string()),pa.field("text",pa.string()),pa.field("vector",pa.list_(pa.float32(),3))])
    table=db.create_table("chunks",schema=schema)
    def supplied():
        upsert(table,[row("a",1,[1.,0.,0.],"ZX-42 red dragon inspection"),
                      row("b",2,[1.,0.,0.],"private other conversation"),
                      row("c",1,[0.,1.,0.],"ordinary unrelated passage")])
        assert table.count_rows()==3
        result=table.search([1.,0.,0.]).distance_type("cosine").where("conversation_id = 1 AND generation = 1 AND space = 'fixture-space'",prefilter=True).limit(1).to_list()
        assert result[0]["row_id"]=="a" and abs(result[0]["_distance"])<1e-6
    checked("external_float32_cosine_prefilter",supplied)
    def retries():
        for _ in range(100):upsert(table,[row("a",1,[1.,0.,0.],"ZX-42 red dragon inspection")])
        assert table.count_rows()==3
    checked("100_idempotent_upserts",retries)
    def text_search():
        table.create_fts_index("text",replace=True)
        assert table.search("dragon",query_type="fts").where("conversation_id = 1",prefilter=True).limit(2).to_list()[0]["row_id"]=="a"
        assert not table.search("dragon",query_type="fts").where("conversation_id = 2",prefilter=True).limit(2).to_list()
    checked("native_fts_and_prefilter",text_search)
    def restart():
        result=subprocess.run([sys.executable,str(Path(__file__)),"--child",str(path),"read"],capture_output=True,text=True,timeout=30)
        assert result.returncode==0,result.stderr
    checked("restart_persistence_and_process_exit",restart)
    def stale():
        old=connect(path).open_table("chunks")
        result=subprocess.run([sys.executable,str(Path(__file__)),"--child",str(path),"crash"],capture_output=True,text=True,timeout=30)
        assert result.returncode==7,result.stderr
        assert old.search().where("row_id = 'c'").to_list()[0]["text"]=="written by another process"
        upsert(table,[row("c",1,[0.,1.,0.],"written by another process")])
        assert table.count_rows()==3
    checked("stale_handle_acknowledgment_lost_recovery",stale)
    def delete():
        table.delete("row_id = 'a'")
        assert table.count_rows()==2
        assert not table.search("dragon",query_type="fts").limit(3).to_list()
    checked("delete_and_fts_visibility",delete)
    def maintenance():
        table.optimize(cleanup_older_than=timedelta(days=14))
        assert table.count_rows()==2
    checked("maintenance_preserves_current_rows",maintenance)
    report={"scope":"Native smoke qualification, not full production acceptance or performance benchmark",
        "python":sys.version,"versions":{d.metadata["Name"]:d.version for d in importlib.metadata.distributions()},
        "checks":checks,"seconds":round(time.perf_counter()-started,3),
        "disk_bytes":sum(p.stat().st_size for p in path.rglob("*") if p.is_file())}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps({"checks":len(checks),"report":str(args.output)}),flush=True)
    return 0

if __name__=="__main__":sys.exit(main())
