"""Explicit Stage 9 vector migration/maintenance commands; no model downloads."""
import argparse
import asyncio
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3

from RAG.index_lifecycle import IndexLifecycle
from RAG.vector_store import EmbeddingSpace,LanceVectorStore
from RAG.vector_migration import VectorMigration,file_hash


def require_offline(stage):
    """Reject supported launcher/ASGI workers before the first rollout transition."""
    try:
        import psutil
    except ImportError as error:
        raise ValueError("Install requirements-vector-dev.txt for worker inspection.") from error
    markers=("run.py","main.py","uvicorn","multiprocessing.spawn")
    busy=[]
    for process in psutil.process_iter(["pid","name"]):
        if process.pid==os.getpid() or "python" not in (process.info["name"] or "").lower():
            continue
        try:
            command=" ".join(process.cmdline()).lower()
            working=Path(process.cwd()).resolve()
            in_stage=working==stage or str(stage).lower() in command
            if in_stage and any(marker in command for marker in markers):
                busy.append(process.pid)
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            raise ValueError("Cannot verify an existing Python worker. Stop it or run with permission to inspect it.") from None
    if busy:
        raise ValueError("Stop Stage 9 launcher/ASGI workers before this transition. PIDs: "+", ".join(map(str,busy)))


def main():
    import main as application
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument("command",choices=("status","prepare","validate","reconcile","activate","rollback","finalize","maintenance","rebuild","retire"))
    parser.add_argument("--database",type=Path,default=application.DATABASE)
    parser.add_argument("--backup-dir",type=Path)
    parser.add_argument("--acceptance",type=Path,help="Complete frozen storage acceptance report, with existing artifact paths")
    parser.add_argument("--decision",action="store_true",help="Explicit rollback/finalization decision; never inferred from elapsed time")
    parser.add_argument("--conversation-id",type=int,help="Authorized conversation for a one-document rebuild")
    parser.add_argument("--file-id",help="Existing authoritative file ID to repair")
    args=parser.parse_args()
    if not args.database.is_absolute() or not args.database.is_file():
        parser.error("Use an existing absolute SQLite database path; launch Stage 9 once for a new project.")
    database=args.database.resolve()
    def connect():
        db=sqlite3.connect(database.as_uri()+"?mode=rw",uri=True,timeout=10)
        db.row_factory=sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db
    try:
        if args.command=="status":
            with closing(connect()) as db:
                exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='vector_control'").fetchone()
                control=dict(db.execute("SELECT * FROM vector_control").fetchone()) if exists else {
                    "backend":"sqlite","lifecycle":"PRE_ACTIVATION","epoch":None}
                if exists:
                    control["operations"]=[dict(r) for r in db.execute(
                        "SELECT kind,state,COUNT(*) count FROM vector_operations GROUP BY kind,state")]
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='vector_builds'").fetchone():
                    control["builds"]=[dict(r) for r in db.execute("SELECT state,COUNT(*) count FROM vector_builds GROUP BY state")]
                print(json.dumps(control,indent=2))
                return 0
        if args.command in ("activate","rollback","finalize","rebuild","retire"):
            require_offline(Path(__file__).resolve().parent)
        store=LanceVectorStore.from_data_dir(database.parent)
        life=IndexLifecycle(connect,database.parent,store)
        life.init()
        backup_dir=args.backup_dir or Path(__file__).resolve().parent/".migration-backups"
        migration=VectorMigration(life,backup_dir)
        if args.command=="prepare":
            result=migration.prepare(lambda file:application.rag.embedding_space(file["config"],file["dimension"]))
        elif args.command=="validate":
            result=migration.validate()
        elif args.command=="reconcile":
            result=life.reconcile()
        elif args.command=="rebuild":
            if args.conversation_id is None or not args.file_id:
                raise ValueError("Rebuild requires --conversation-id and --file-id.")
            from RAG.vector_recovery import VectorRecovery
            result=asyncio.run(VectorRecovery(life,application.rag).rebuild_document(args.conversation_id,args.file_id))
        elif args.command=="activate":
            if args.acceptance is None:
                raise ValueError("Activation requires the complete frozen acceptance report.")
            report=json.loads(args.acceptance.read_text(encoding="utf-8"))
            artifacts={}
            for value in report.get("artifacts",[]):
                path=Path(value)
                if not path.is_absolute() or not path.is_file():
                    raise ValueError("Acceptance artifacts must be existing absolute file paths.")
                artifacts[str(path)]=file_hash(path)
            report["artifact_hashes"]=artifacts
            migration.activate(report)
            result=life.control()
        elif args.command=="rollback":
            migration.rollback(decision=args.decision)
            result=life.control()
        elif args.command=="retire":
            result=migration.retire(decision=args.decision)
        elif args.command=="finalize":
            result=migration.finalize(decision=args.decision)
        else:
            # Compaction without pruning; migration snapshots retain their versions.
            with closing(connect()) as db:
                spaces=[EmbeddingSpace(**json.loads(r[0])) for r in db.execute(
                    "SELECT specification FROM vector_spaces LIMIT 40")]
            result=[store.maintenance(space,allow_cleanup=False) for space in spaces]
        print(json.dumps(result,indent=2))
        return 0
    except (ValueError,RuntimeError,sqlite3.Error) as error:
        parser.exit(2,str(error)+"\n")


if __name__=="__main__":
    raise SystemExit(main())
