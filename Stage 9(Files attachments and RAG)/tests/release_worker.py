"""Fresh-process source mutation helper for release-gate race tests."""
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from RAG.documents import Documents

if __name__=="__main__":
    database,data_dir,cid,fid=sys.argv[1:]
    def connect():
        db=sqlite3.connect(database,timeout=10);db.row_factory=sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON");return db
    docs=Documents(connect,lambda:Path(data_dir))
    print("ready",flush=True)
    docs.delete(int(cid),fid)
    print("committed",flush=True)
