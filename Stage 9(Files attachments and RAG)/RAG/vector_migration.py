"""Explicit migration operations. No implicit startup cutover or destructive cleanup."""
from contextlib import closing, contextmanager
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from .storage_gate import StorageBusy
from .vector_store import EmbeddingSpace, GenerationScope, IndexInconsistent, canonical, normalized, digest


class MigrationError(ValueError):
    pass


def file_hash(path):
    result=hashlib.sha256()
    with open(path,"rb") as handle:
        for block in iter(lambda:handle.read(1024*1024),b""):
            result.update(block)
    return result.hexdigest()


class VectorMigration:
    def __init__(self,lifecycle,backup_directory,stability_days=14):
        self.life=lifecycle
        self.backups=Path(backup_directory)
        if not self.backups.is_absolute():
            raise ValueError("Migration backups require an absolute directory.")
        self.backups=self.backups.resolve()
        active=(self.life.directory,self.life.store.directory)
        if any(self.backups==p or p in self.backups.parents for p in active):
            raise ValueError("Backups must be outside the active database directories.")
        if not isinstance(stability_days,int) or stability_days<14:
            raise ValueError("The stabilization period must be at least 14 days.")
        self.stability_days=stability_days

    @contextmanager
    def exclusive(self,timeout=30):
        deadline=time.monotonic()+timeout
        while True:
            with self.life.gate.hold():
                try:
                    self.life.drain_dead_leases()
                except StorageBusy as error:
                    busy=error
                else:
                    yield
                    return
            # Release the gate so an active worker can finish and release its lease.
            if time.monotonic()>=deadline:
                raise busy
            time.sleep(.02)

    def backup(self):
        self.backups.mkdir(parents=True,exist_ok=True)
        path=self.backups/("sqlite-"+uuid4().hex+".db")
        with closing(self.life.connect()) as source, closing(sqlite3.connect(path)) as target:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0]!="ok" or target.execute("PRAGMA foreign_key_check").fetchone():
                raise MigrationError("Recovery backup integrity verification failed.")
            # Read the SNAPSHOT's generations, not a later live database state.
            target.row_factory=sqlite3.Row
            control=dict(target.execute("SELECT * FROM vector_control WHERE singleton=1").fetchone())
            generations=[dict(row) for row in target.execute("""SELECT g.file_id,g.generation,g.space,g.sources,g.manifest,g.state,s.specification
                FROM vector_generations g JOIN files f ON f.id=g.file_id AND f.index_generation=g.generation
                JOIN vector_spaces s ON s.id=g.space ORDER BY g.file_id,g.space""")]
        return {"path":str(path),"sha256":file_hash(path),"created_at":time.time(),
                "backend":control["backend"],"epoch":control["epoch"],"lifecycle":control["lifecycle"],
                "generation_manifest_digest":digest(generations),"generations":len(generations),
                "embedding_spaces":list(dict.fromkeys(row["specification"] for row in generations)),
                "recovery":"Restore authoritative SQLite, then verify/rebuild compatible vector generations; a native table version alone is not proof of health."}

    @staticmethod
    def verify_backup(record):
        path=Path(record["path"])
        if not path.is_file() or file_hash(path)!=record["sha256"]:
            raise MigrationError("Verified recovery backup is missing or changed.")

    def _record(self, **changes):
        with closing(self.life.connect()) as db, db:
            control=self.life.control(db)
            manifest=json.loads(control["manifest"])
            manifest.update(changes)
            db.execute("UPDATE vector_control SET manifest=? WHERE singleton=1",(canonical(manifest),))
            return manifest

    def prepare(self,space_resolver):
        """Transfer compatible legacy vectors; never silently re-embed a new digest.

        space_resolver validates legacy config/dimensions against the installed
        representation contract. Unknown configurations fail with data intact.
        """
        with self.exclusive():
            if self.life.control()["lifecycle"]!="PRE_ACTIVATION":
                raise MigrationError("Preparation is only available before activation.")
            backup=self.backup()
            self._record(preparation="started",backup=backup)
        inventory={"files":0,"sources":0,"vectors":0}
        try:
            with self.life.operation("migration",ttl=1800) as lease:
                with closing(self.life.connect()) as db:
                    files=[dict(r) for r in db.execute("""SELECT DISTINCT f.* FROM files f
                        JOIN chunks c ON c.file_id=f.id ORDER BY f.id""")]
                for file in files:
                    self.life.renew(lease,1800)
                    space=space_resolver(file)
                    with closing(self.life.connect()) as db:
                        legacy={r["id"]:dict(r) for r in db.execute("SELECT * FROM chunks WHERE file_id=?",(file["id"],))}
                    if any(r["config"]!=file["config"] for r in legacy.values()):
                        raise MigrationError("Legacy source configurations are inconsistent; re-index explicitly.")
                    op,scope,records=self.life.stage(lease,file["conversation_id"],file["id"],space)
                    for row in records:
                        row["vector"]=normalized(json.loads(legacy[row["id"]]["vector"]),space.dimensions)
                    self.life.store.initialize_index(space)
                    receipt=self.life.store.upsert_chunks(space,scope,records)
                    self.life.publish(lease,op,space,receipt)
                    inventory["files"]+=1;inventory["sources"]+=len(records);inventory["vectors"]+=len(records)
            self._record(preparation="verified",inventory=inventory)
            self.validate()
            return inventory
        except Exception as error:
            self._record(preparation="failed",failure=type(error).__name__)
            raise

    def validate(self):
        """Compare actual manifests with current authoritative sources, not versions."""
        with closing(self.life.connect()) as db:
            groups=[dict(r) for r in db.execute("""SELECT f.id,f.conversation_id,f.index_generation,g.space,s.specification
                FROM files f JOIN chunks c ON c.file_id=f.id
                LEFT JOIN vector_generations g ON g.file_id=f.id AND g.generation=f.index_generation AND g.state='ready'
                LEFT JOIN vector_spaces s ON s.id=g.space GROUP BY f.id,g.space""")]
        counts={"files":0,"sources":0}
        with self.life.operation("validation",ttl=1800) as lease:
            for group in groups:
                if not group["specification"]:
                    raise MigrationError("An authoritative document lacks a validated current generation.")
                space=EmbeddingSpace(**json.loads(group["specification"]))
                snapshots=self.life.snapshot(lease,group["conversation_id"],[group["id"]],space)
                counts["files"]+=1;counts["sources"]+=len(snapshots[0][1])
        return counts

    @staticmethod
    def _acceptance(report):
        # A numeric smoke benchmark cannot stand in for all migration requirements.
        required={"runtime","regression","populated_migration","authorization","citations",
                  "recovery","publication","retrieval_quality","performance","offline_worker_rollout"}
        if report.get("frozen_version")!=1 or set(report.get("checks",{}))!=required:
            raise MigrationError("Complete frozen storage acceptance evidence is required for cutover.")
        if any(value is not True for value in report["checks"].values()):
            raise MigrationError("A mandatory storage acceptance check has not passed.")
        if not report.get("artifacts"):
            raise MigrationError("Acceptance results need reproducible artifact references.")

    def activate(self,acceptance):
        self._acceptance(acceptance)
        with self.exclusive():
            control=self.life.control()
            if control["lifecycle"]!="PRE_ACTIVATION":
                raise MigrationError("Cutover is only available from PRE_ACTIVATION.")
            manifest=json.loads(control["manifest"])
            if manifest.get("preparation")!="verified":
                raise MigrationError("Migration preparation has not been verified.")
            self.verify_backup(manifest["backup"])
            self.validate()
            manifest["acceptance"]=acceptance
            manifest["cleanup"]="pending explicit decision after stabilization"
            self.life._set_backend(control["epoch"],"lancedb","CUTOVER_ACTIVE",manifest)

    def rollback(self, *, decision=False):
        """Before finalization only: preserve CURRENT application records."""
        if not decision:
            raise MigrationError("Rollback requires an explicit decision.")
        with self.exclusive():
            control=self.life.control()
            if control["lifecycle"]!="CUTOVER_ACTIVE":
                raise MigrationError("Ordinary rollback is unavailable; repair/rebuild the vector index.")
            self.validate()
            backup=self.backup()
            with closing(self.life.connect()) as db:
                rows=[dict(r) for r in db.execute("""SELECT g.*,s.specification FROM vector_generations g
                    JOIN files f ON f.id=g.file_id AND f.index_generation=g.generation
                    JOIN vector_spaces s ON s.id=g.space WHERE g.state='ready'""")]
            restored=[]
            for row in rows:
                space=EmbeddingSpace(**json.loads(row["specification"]))
                scope=GenerationScope(**json.loads(row["scope"]))
                exported=self.life.store.export_generation(space,scope,json.loads(row["manifest"]))
                restored.extend((json.dumps(r["vector"]),r["source_id"],scope) for r in exported)
            # All current chats/branches remain untouched. Only vector payloads return.
            with closing(self.life.connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                if "vector" not in {r["name"] for r in db.execute("PRAGMA table_info(chunks)")}:
                    raise MigrationError("Legacy vector schema was removed; reverse migration is unavailable.")
                for _,_,scope in restored:
                    file=db.execute("SELECT index_generation,sha256 FROM files WHERE id=?",(scope.file_id,)).fetchone()
                    if not file or tuple(file)!=(scope.generation,scope.file_fingerprint):
                        raise MigrationError("Source changed during rollback.")
                manifest=json.loads(control["manifest"]);manifest["rollback_backup"]=backup
                db.execute("""UPDATE vector_control SET backend='sqlite',lifecycle='PRE_ACTIVATION',
                    epoch=epoch+1,manifest=? WHERE singleton=1""",(canonical(manifest),))
                db.executemany("UPDATE chunks SET vector=? WHERE id=?",((raw,sid) for raw,sid,_ in restored))

    def finalize(self, *, decision=False):
        if not decision:
            raise MigrationError("Final cleanup requires an explicit decision.")
        with self.exclusive():
            control=self.life.control()
            if control["lifecycle"]!="CUTOVER_ACTIVE":
                raise MigrationError("Finalization requires a validated active dedicated backend.")
            if time.time()-control["activated_at"] < self.stability_days*86400:
                raise MigrationError("The stabilization period has not elapsed.")
            manifest=json.loads(control["manifest"])
            self.verify_backup(manifest["backup"])
            self.validate()
            backup=self.backup()
            with closing(self.life.connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                # Never drop an unknown trigger/view/index or assume no dependency.
                dependencies=[dict(r) for r in db.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL")
                    if r["name"] not in ("chunks","vector_write_guard_insert","vector_write_guard_update") and "vector" in (r["sql"] or "").lower()
                    and "chunks" in (r["sql"] or "").lower()]
                if dependencies:
                    raise MigrationError("Legacy vector dependencies require an inspected table-rebuild migration.")
                fields={r["name"] for r in db.execute("PRAGMA table_info(chunks)")}
                if "vector" not in fields:
                    raise MigrationError("Unexpected legacy schema; inspect before cleanup.")
                db.execute("DROP TRIGGER IF EXISTS vector_write_guard_insert")
                db.execute("DROP TRIGGER IF EXISTS vector_write_guard_update")
                db.execute("ALTER TABLE chunks DROP COLUMN vector")
                if db.execute("PRAGMA foreign_key_check").fetchone() or db.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
                    raise MigrationError("Schema verification failed; cleanup rolled back.")
                manifest["finalization_backup"]=backup
                manifest["cleanup"]="schema verified; retirement still requires explicit decision"
                # Schema cleanup and lifecycle/epoch commit atomically.
                db.execute("""UPDATE vector_control SET lifecycle='FINALIZED',epoch=epoch+1,manifest=?
                    WHERE singleton=1""",(canonical(manifest),))
            # Reopen, as startup will, and verify authoritative source schema.
            with closing(self.life.connect()) as db:
                if "vector" in {r["name"] for r in db.execute("PRAGMA table_info(chunks)")}:
                    raise MigrationError("Final schema still contains embeddings.")
            return {"lifecycle":"FINALIZED","backup":backup}

    def retire(self, *, decision=False):
        """Explicit, resumable retirement of named migration backups only.

        Keep a new vector-free authoritative recovery snapshot. Never recursively
        delete directories or infer permission to delete from elapsed time.
        """
        if not decision:
            raise MigrationError("Backup retirement requires a separate explicit decision.")
        with self.exclusive():
            control=self.life.control()
            if control["lifecycle"]!="FINALIZED":
                raise MigrationError("Backup retirement requires completed live finalization.")
            self.validate()
            manifest=json.loads(control["manifest"])
            retirement=manifest.get("retirement")
            if retirement is None:
                artifacts=[manifest[key] for key in ("backup","rollback_backup","finalization_backup") if key in manifest]
                if not artifacts:
                    raise MigrationError("No verified migration backup inventory is available.")
                for record in artifacts:
                    path=Path(record["path"]).resolve()
                    if path.parent!=self.backups or not path.name.startswith("sqlite-") or path.suffix!=".db":
                        raise MigrationError("Retirement target is outside the configured migration backup directory.")
                    if time.time()-record["created_at"]<14*86400:
                        raise MigrationError("Every rollback snapshot must be retained for at least 14 days.")
                    self.verify_backup(record)
                recovery=self.backup()
                retirement={"state":"pending","artifacts":artifacts,"removed":[],"recovery_backup":recovery}
                self._record(retirement=retirement)
            self.verify_backup(retirement["recovery_backup"])
            for record in retirement["artifacts"]:
                path=Path(record["path"]).resolve()
                if path.parent!=self.backups or path==Path(retirement["recovery_backup"]["path"]).resolve():
                    raise MigrationError("Unsafe backup retirement target.")
                if time.time()-record["created_at"]<14*86400:
                    raise MigrationError("Backup retention has not elapsed.")
                if path.exists():
                    self.verify_backup(record)
                    path.unlink()
                # An absent target is an idempotent retry after unlink/ack loss.
                if str(path) not in retirement["removed"]:
                    retirement["removed"].append(str(path))
                self._record(retirement=retirement)
            retirement["state"]="complete"
            manifest=self._record(retirement=retirement)
            self.life._set_backend(control["epoch"],"lancedb","RETIRED",manifest)
            return {"lifecycle":"RETIRED","retired":len(retirement["removed"]),
                    "recovery_backup":retirement["recovery_backup"]}
