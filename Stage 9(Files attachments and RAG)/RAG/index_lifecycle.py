"""Durable publication metadata and fencing for the dedicated search index.

This is additive application metadata, never vector storage. Integrating the
runtime and migrating live data are separate, explicit steps.
"""
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import time
from uuid import uuid4

from .storage_gate import ProcessGate, StorageBusy
from .vector_store import (EmbeddingSpace, GenerationScope, IndexInconsistent,
    IndexUnavailable, canonical, digest, row_identity, source_fingerprint)

PROTOCOL = 1
STATES = ("PRE_ACTIVATION", "CUTOVER_ACTIVE", "FINALIZED", "RETIRED")


class EpochChanged(IndexUnavailable):
    pass


@dataclass(frozen=True)
class Lease:
    token: str
    epoch: int
    backend: str


class IndexLifecycle:
    def __init__(self, connect, data_dir, store):
        self.connect = connect
        self.directory = Path(data_dir).resolve()
        self.store = store
        self.gate = ProcessGate(self.directory/"coord"/"backend.lock")

    def init(self):
        with self.gate.hold(), closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS vector_control(
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    epoch INTEGER NOT NULL, backend TEXT NOT NULL,
                    lifecycle TEXT NOT NULL, minimum_protocol INTEGER NOT NULL,
                    activated_at REAL, manifest TEXT NOT NULL DEFAULT '{}');
                INSERT OR IGNORE INTO vector_control(singleton,epoch,backend,lifecycle,minimum_protocol)
                    VALUES(1,0,'sqlite','PRE_ACTIVATION',1);
                CREATE TABLE IF NOT EXISTS vector_leases(
                    token TEXT PRIMARY KEY, epoch INTEGER NOT NULL, backend TEXT NOT NULL,
                    protocol INTEGER NOT NULL, pid INTEGER NOT NULL, kind TEXT NOT NULL,
                    expires_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS vector_spaces(
                    id TEXT PRIMARY KEY, specification TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS vector_generations(
                    file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                    generation INTEGER NOT NULL, space TEXT NOT NULL REFERENCES vector_spaces(id),
                    scope TEXT NOT NULL, sources TEXT NOT NULL,
                    manifest TEXT, destination_version INTEGER, state TEXT NOT NULL,
                    PRIMARY KEY(file_id,generation,space));
                CREATE TABLE IF NOT EXISTS vector_operations(
                    id TEXT PRIMARY KEY, file_id TEXT NOT NULL, generation INTEGER NOT NULL,
                    space TEXT NOT NULL, scope TEXT NOT NULL, epoch INTEGER NOT NULL,
                    state TEXT NOT NULL, kind TEXT NOT NULL, error TEXT,
                    updated_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS vector_builds(
                    id TEXT PRIMARY KEY, file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                    generation INTEGER NOT NULL, scope TEXT NOT NULL, config TEXT NOT NULL,
                    state TEXT NOT NULL, error TEXT, updated_at REAL NOT NULL);
                CREATE TRIGGER IF NOT EXISTS vector_file_generation AFTER UPDATE OF index_generation ON files
                    WHEN NEW.index_generation != OLD.index_generation BEGIN
                    UPDATE vector_generations SET state='stale' WHERE file_id=NEW.id
                        AND generation!=NEW.index_generation;
                END;
                CREATE TRIGGER IF NOT EXISTS vector_delete_outbox BEFORE DELETE ON files BEGIN
                    INSERT OR IGNORE INTO vector_operations(id,file_id,generation,space,scope,epoch,state,kind,updated_at)
                        SELECT 'delete:' || file_id || ':' || generation || ':' || space,
                            file_id,generation,space,scope,(SELECT epoch FROM vector_control WHERE singleton=1),
                            'pending','delete',unixepoch() FROM vector_generations WHERE file_id=OLD.id;
                END;
            """)

            fields={r["name"] for r in db.execute("PRAGMA table_info(chunks)")}
            if "vector" in fields:
                db.execute("""CREATE TRIGGER IF NOT EXISTS vector_write_guard_insert BEFORE INSERT ON chunks
                    WHEN (SELECT backend FROM vector_control WHERE singleton=1)!='sqlite' AND NEW.vector!='[]'
                    BEGIN SELECT RAISE(ABORT,'Legacy vector writes are disabled after cutover'); END""")
                db.execute("""CREATE TRIGGER IF NOT EXISTS vector_write_guard_update BEFORE UPDATE OF vector ON chunks
                    WHEN (SELECT backend FROM vector_control WHERE singleton=1)!='sqlite'
                    AND NEW.vector IS NOT OLD.vector AND NEW.vector!='[]'
                    BEGIN SELECT RAISE(ABORT,'Legacy vector writes are disabled after cutover'); END""")

    def control(self, db=None):
        with closing(self.connect()) if db is None else _borrow(db) as conn:
            row = conn.execute("SELECT * FROM vector_control WHERE singleton=1").fetchone()
            if row is None or row["minimum_protocol"] > PROTOCOL:
                raise EpochChanged("This worker is incompatible with the active storage backend.")
            return dict(row)

    @contextmanager
    def operation(self, kind, ttl=180):
        if not 1 <= ttl <= 1800:
            raise ValueError("Invalid storage lease duration.")
        token = uuid4().hex
        # A live OS reservation cannot be mistaken for a dead worker just because
        # its heartbeat expired. Different operations use different reservations.
        reservation = ProcessGate(self.directory/"coord"/"leases"/(token+".lock"))
        try:
            with reservation.hold():
                with self.gate.hold(), closing(self.connect()) as db, db:
                    db.execute("BEGIN IMMEDIATE")
                    control = self.control(db)
                    lease = Lease(token, control["epoch"], control["backend"])
                    db.execute("INSERT INTO vector_leases VALUES(?,?,?,?,?,?,?)",
                        (token,lease.epoch,lease.backend,PROTOCOL,os.getpid(),kind,time.time()+ttl))
                try:
                    yield lease
                finally:
                    with self.gate.hold(), closing(self.connect()) as db, db:
                        db.execute("DELETE FROM vector_leases WHERE token=?", (token,))
        finally:
            # UUID reservations are never reused. Remove a finished reservation
            # only after its OS handle closes and its SQL lease is gone.
            with self.gate.hold(), closing(self.connect()) as db:
                if not db.execute("SELECT 1 FROM vector_leases WHERE token=?",(token,)).fetchone():
                    reservation.path.unlink(missing_ok=True)

    def check(self, lease, db=None):
        with closing(self.connect()) if db is None else _borrow(db) as conn:
            control = self.control(conn)
            row = conn.execute("SELECT * FROM vector_leases WHERE token=?", (lease.token,)).fetchone()
            if (control["epoch"] != lease.epoch or control["backend"] != lease.backend or row is None
                or row["epoch"] != lease.epoch or row["expires_at"] <= time.time()):
                raise EpochChanged("Storage backend or lease changed; retry the operation.")
            return control

    def renew(self, lease, ttl=180):
        if not 1 <= ttl <= 1800:
            raise ValueError("Invalid storage lease duration.")
        with self.gate.hold(), closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            self.check(lease, db)
            db.execute("UPDATE vector_leases SET expires_at=? WHERE token=?", (time.time()+ttl,lease.token))

    @staticmethod
    def _sources(db, cid, fid):
        row = db.execute("SELECT * FROM files WHERE id=? AND conversation_id=?", (fid,cid)).fetchone()
        if row is None:
            raise IndexUnavailable("Document is no longer authorized or available.")
        scope = GenerationScope(cid,fid,row["index_generation"],row["sha256"])
        rows = [dict(r) for r in db.execute(
            "SELECT id,file_id,chunk_index,text,metadata,config FROM chunks WHERE file_id=? ORDER BY chunk_index",(fid,))]
        if not rows:
            raise IndexUnavailable("No authoritative source records are ready for indexing.")
        sources = {}
        for r in rows:
            r["metadata"] = json.loads(r["metadata"])
            sources[r["id"]] = source_fingerprint(r["text"],r["metadata"])
        return scope, sources, rows

    def begin_build_transaction(self, db, lease, cid, fid, config):
        """Durable intent before embedding dimensions are known; contains no vectors."""
        self.check(lease,db)
        scope,sources,rows=self._sources(db,cid,fid)
        build_id=digest([asdict(scope),config])
        db.execute("UPDATE vector_builds SET state='superseded' WHERE file_id=? AND id!=? AND state='pending'",
                   (fid,build_id))
        db.execute("""INSERT INTO vector_builds(id,file_id,generation,scope,config,state,updated_at)
            VALUES(?,?,?,?,?,'pending',?) ON CONFLICT(id) DO UPDATE SET
            state='pending',error=NULL,updated_at=excluded.updated_at""",
            (build_id,fid,scope.generation,canonical(asdict(scope)),config,time.time()))
        return build_id,scope,sources

    def check_build(self, lease, scope, sources):
        with closing(self.connect()) as db:
            db.execute("BEGIN")
            self.check(lease,db)
            actual,fingerprints,_=self._sources(db,scope.conversation_id,scope.file_id)
            if actual!=scope or fingerprints!=sources:
                raise IndexInconsistent("Document changed while embeddings were being generated.")

    def stage(self, lease, cid, fid, space):
        with self.gate.hold(), closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            return self.stage_transaction(db,lease,cid,fid,space)

    def stage_transaction(self, db, lease, cid, fid, space):
        """Called within the same transaction that mutates authoritative chunks."""
        self.check(lease,db)
        scope,sources,rows = self._sources(db,cid,fid)
        operation_id = digest([lease.epoch,asdict(scope),space.key])
        db.execute("INSERT OR IGNORE INTO vector_spaces VALUES(?,?)",(space.key,canonical(asdict(space))))
        db.execute("""INSERT OR IGNORE INTO vector_generations(file_id,generation,space,scope,sources,state)
            VALUES(?,?,?,?,?,'pending')""",(fid,scope.generation,space.key,canonical(asdict(scope)),canonical(sources)))
        prior = db.execute("SELECT sources FROM vector_generations WHERE file_id=? AND generation=? AND space=?",
            (fid,scope.generation,space.key)).fetchone()
        if json.loads(prior[0]) != sources:
            raise IndexInconsistent("Sources changed without advancing their generation.")
        db.execute("""INSERT OR IGNORE INTO vector_operations
            (id,file_id,generation,space,scope,epoch,state,kind,updated_at)
            VALUES(?,?,?,?,?,?,'pending','upsert',?)""",
            (operation_id,fid,scope.generation,space.key,canonical(asdict(scope)),lease.epoch,time.time()))
        return operation_id,scope,rows

    def publish(self, lease, operation_id, space, receipt):
        """Destination write is already complete; publication is compare-and-swap."""
        with self.gate.hold():
            with closing(self.connect()) as db:
                self.check(lease,db)
                operation = db.execute("SELECT * FROM vector_operations WHERE id=?",(operation_id,)).fetchone()
                if operation is None or operation["epoch"] != lease.epoch or operation["space"] != space.key:
                    raise EpochChanged("Unknown or obsolete publication operation.")
                scope = GenerationScope(**json.loads(operation["scope"]))
            # Never hold a SQLite transaction across native vector I/O.
            self.store.verify_index_consistency(space,scope,receipt["manifest"])
            with closing(self.connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                self.check(lease,db)
                actual,sources,_ = self._sources(db,scope.conversation_id,scope.file_id)
                expected = {row_identity(space,scope,sid):fp for sid,fp in sources.items()}
                manifest = receipt["manifest"]
                if (actual != scope or set(expected) != set(manifest) or
                    any(manifest[key]["source_fingerprint"] != fp for key,fp in expected.items())):
                    raise IndexInconsistent("Authoritative document changed before generation publication.")
                db.execute("""UPDATE vector_generations SET state='ready',manifest=?,destination_version=?
                    WHERE file_id=? AND generation=? AND space=?""",
                    (canonical(manifest),receipt["version"],scope.file_id,scope.generation,space.key))
                db.execute("UPDATE vector_operations SET state='complete',error=NULL,updated_at=? WHERE id=?",
                    (time.time(),operation_id))
                for old in db.execute("""SELECT * FROM vector_generations WHERE file_id=?
                        AND generation!=? AND state='stale'""",(scope.file_id,scope.generation)).fetchall():
                    key="delete:"+old["file_id"]+":"+str(old["generation"])+":"+old["space"]
                    db.execute("""INSERT OR IGNORE INTO vector_operations
                        (id,file_id,generation,space,scope,epoch,state,kind,updated_at)
                        VALUES(?,?,?,?,?,?,'pending','delete',?)""",
                        (key,old["file_id"],old["generation"],old["space"],old["scope"],lease.epoch,time.time()))


    def snapshot(self, lease, cid, file_ids, space):
        if not file_ids or len(file_ids)>40 or len(set(file_ids))!=len(file_ids):
            raise ValueError("Invalid selected-file scope.")
        with closing(self.connect()) as db:
            db.execute("BEGIN")
            self.check(lease,db)
            snapshots=[]
            for fid in file_ids:
                scope,sources,_ = self._sources(db,cid,fid)
                row = db.execute("""SELECT * FROM vector_generations
                    WHERE file_id=? AND generation=? AND space=? AND state='ready'""",
                    (fid,scope.generation,space.key)).fetchone()
                if row is None or json.loads(row["sources"]) != sources:
                    raise IndexUnavailable("Selected document index is pending, degraded, or incompatible.")
                snapshots.append((scope,json.loads(row["manifest"])))
        for scope,manifest in snapshots:
            self.store.verify_index_consistency(space,scope,manifest,verify_vectors=False)
        return snapshots

    def search(self, lease, cid, file_ids, space, query_vector, limit=30, source_ids=None):
        snapshots = self.snapshot(lease,cid,file_ids,space)
        candidates = self.store.search_vectors(space,[s for s,_ in snapshots],query_vector,limit,source_ids)
        for scope,manifest in snapshots:
            self.store.verify_index_consistency(space,scope,manifest,verify_vectors=False)
        return self.hydrate(lease,space,snapshots,candidates)

    def hydrate(self, lease, space, snapshots, candidates):
        """Final passage text always comes from authoritative, current SQLite rows."""
        with self.gate.hold(), closing(self.connect()) as db:
            db.execute("BEGIN")
            self.check(lease,db)
            authorized = {}
            for scope,manifest in snapshots:
                actual,_,_ = self._sources(db,scope.conversation_id,scope.file_id)
                if actual != scope:
                    raise IndexInconsistent("Selected document changed during retrieval.")
                published = db.execute("""SELECT manifest FROM vector_generations
                    WHERE file_id=? AND generation=? AND space=? AND state='ready'""",
                    (scope.file_id,scope.generation,space.key)).fetchone()
                if not published or json.loads(published[0]) != manifest:
                    raise IndexInconsistent("Generation is no longer published.")
                authorized[scope.file_id] = (scope,manifest)
            evidence=[]
            for c in candidates:
                if c["file_id"] not in authorized:
                    raise IndexInconsistent("Vector index returned an unauthorized document.")
                scope,manifest = authorized[c["file_id"]]
                key=row_identity(space,scope,c["source_id"])
                if (c["row_id"] != key or c["space"] != space.key or key not in manifest or
                    c["conversation_id"] != scope.conversation_id or c["generation"] != scope.generation or
                    c["file_fingerprint"] != scope.file_fingerprint):
                    raise IndexInconsistent("Vector result does not belong to the authorized generation.")
                row=db.execute("""SELECT c.id,c.file_id,c.chunk_index,c.text,c.metadata,c.config,f.original_filename
                    FROM chunks c JOIN files f ON f.id=c.file_id WHERE c.id=? AND c.file_id=? AND f.conversation_id=?""",
                    (c["source_id"],scope.file_id,scope.conversation_id)).fetchone()
                if row is None:
                    raise IndexInconsistent("Vector source no longer exists.")
                row=dict(row)
                fp=source_fingerprint(row["text"],json.loads(row["metadata"]))
                if fp != c["source_fingerprint"] or fp != manifest[key]["source_fingerprint"]:
                    raise IndexInconsistent("Vector source fingerprint no longer matches.")
                row["dense_score"]=c["score"]
                row["source_revision"]={"source_id":row["id"],"generation":scope.generation,"file_fingerprint":scope.file_fingerprint,
                    "source_fingerprint":fp,"epoch":lease.epoch}
                evidence.append(row)
            self.check(lease,db)
            return evidence

    def reconcile(self, limit=8, seconds=20):
        """Bounded, idempotent maintenance; no model calls and no model substitution."""
        if not 1<=limit<=40 or not 0<seconds<=60:
            raise ValueError("Invalid reconciliation bounds.")
        deadline=time.monotonic()+seconds
        result={"recovered":0,"deleted":0,"degraded":0}
        with self.operation("reconciliation",ttl=180) as lease:
            with closing(self.connect()) as db:
                work=[dict(r) for r in db.execute("""SELECT o.*,s.specification FROM vector_operations o
                    JOIN vector_spaces s ON s.id=o.space WHERE o.state='pending' OR (o.kind='delete' AND o.state='complete')
                    ORDER BY CASE o.state WHEN 'pending' THEN 0 ELSE 1 END,
                    CASE o.kind WHEN 'delete' THEN 0 ELSE 1 END,o.updated_at LIMIT ?""",(limit,))]
            for operation in work:
                if time.monotonic()>=deadline:
                    break
                self.renew(lease)
                space=EmbeddingSpace(**json.loads(operation["specification"]))
                scope=GenerationScope(**json.loads(operation["scope"]))
                try:
                    if operation["kind"]=="delete":
                        with self.gate.hold():
                            with closing(self.connect()) as db:
                                active=db.execute("""SELECT 1 FROM vector_generations g JOIN files f ON f.id=g.file_id
                                    WHERE g.file_id=? AND g.generation=? AND g.space=? AND g.state='ready'
                                    AND f.index_generation=g.generation""",
                                    (scope.file_id,scope.generation,space.key)).fetchone()
                            if active:
                                raise IndexInconsistent("Refusing to remove a current published generation.")
                            removed=self.store.delete_document_vectors(space,scope)
                            with closing(self.connect()) as db,db:
                                self.check(lease,db)
                                db.execute("UPDATE vector_operations SET state='complete',error=NULL,updated_at=? WHERE id=?",(time.time(),operation["id"]))
                                db.execute("DELETE FROM vector_generations WHERE file_id=? AND generation=? AND space=?",
                                    (scope.file_id,scope.generation,space.key))
                            result["deleted"]+=int(removed)
                    else:
                        with closing(self.connect()) as db:
                            owner=db.execute("SELECT conversation_id,index_generation,sha256 FROM files WHERE id=?",(scope.file_id,)).fetchone()
                            obsolete=owner is None or tuple(owner)!=(scope.conversation_id,scope.generation,scope.file_fingerprint)
                            if not obsolete:
                                actual,sources,_=self._sources(db,scope.conversation_id,scope.file_id)
                                obsolete=actual!=scope
                        if obsolete:
                            # A writer may have finished AFTER an earlier delete was
                            # acknowledged. Re-arm cleanup rather than orphaning rows.
                            with self.gate.hold(),closing(self.connect()) as db,db:
                                self.check(lease,db)
                                key="delete:"+scope.file_id+":"+str(scope.generation)+":"+space.key
                                db.execute("""INSERT INTO vector_operations(id,file_id,generation,space,scope,epoch,state,kind,updated_at)
                                    VALUES(?,?,?,?,?,?,'pending','delete',?) ON CONFLICT(id) DO UPDATE SET
                                    state='pending',error=NULL,updated_at=excluded.updated_at""",
                                    (key,scope.file_id,scope.generation,space.key,canonical(asdict(scope)),lease.epoch,time.time()))
                                db.execute("UPDATE vector_operations SET state='superseded' WHERE id=?",(operation["id"],))
                            continue
                        # Re-stage under the CURRENT epoch; never revive an old worker lease.
                        op,_,_=self.stage(lease,scope.conversation_id,scope.file_id,space)
                        receipt=self.store.recover_generation(space,scope,sources)
                        with closing(self.connect()) as db:
                            prior=db.execute("SELECT manifest FROM vector_generations WHERE file_id=? AND generation=? AND space=?",
                                             (scope.file_id,scope.generation,space.key)).fetchone()
                        if prior and prior["manifest"] and json.loads(prior["manifest"])!=receipt["manifest"]:
                            raise IndexInconsistent("Stored vectors differ from the published manifest; explicit repair is required.")
                        self.publish(lease,op,space,receipt)
                        if op!=operation["id"]:
                            with closing(self.connect()) as db,db:
                                db.execute("UPDATE vector_operations SET state='superseded' WHERE id=?",(operation["id"],))
                        result["recovered"]+=1
                except (IndexUnavailable,ValueError) as error:
                    with closing(self.connect()) as db,db:
                        db.execute("UPDATE vector_operations SET error=?,updated_at=? WHERE id=?",
                            (type(error).__name__,time.time(),operation["id"]))
                    result["degraded"]+=1
        return result

    def drain_dead_leases(self):
        """Call under the backend gate. Expiry alone never proves process death."""
        with closing(self.connect()) as db, db:
            for row in db.execute("SELECT token FROM vector_leases").fetchall():
                # Registry reentrancy must not treat our own active operation as dead.
                token=row["token"]
                path=self.directory/"coord"/"leases"/(token+".lock")
                gate=ProcessGate(path,timeout=.001)
                if getattr(gate.state.local,"depth",0):
                    raise StorageBusy("This process still owns a storage operation.")
                try:
                    with gate.hold():
                        db.execute("DELETE FROM vector_leases WHERE token=?",(token,))
                except StorageBusy:
                    raise StorageBusy("A worker still owns an operation; drain it before cutover.") from None

    def _set_backend(self, expected_epoch, backend, lifecycle, manifest):
        """Internal migration primitive. Caller must validate coverage/offline rollout.

        Not an application route or a complete migration command.
        """
        if backend not in ("sqlite","lancedb") or lifecycle not in STATES:
            raise ValueError("Invalid backend transition.")
        with self.gate.hold():
            self.drain_dead_leases()
            with closing(self.connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                current=self.control(db)
                if current["epoch"] != expected_epoch:
                    raise EpochChanged("Another migration changed the backend.")
                if current["lifecycle"] in ("FINALIZED","RETIRED") and backend=="sqlite":
                    raise ValueError("Reverse migration is not implemented; repair or rebuild LanceDB.")
                valid = {
                    "PRE_ACTIVATION": {("lancedb","CUTOVER_ACTIVE")},
                    "CUTOVER_ACTIVE": {("sqlite","PRE_ACTIVATION"),("lancedb","FINALIZED")},
                    "FINALIZED": {("lancedb","RETIRED")},
                    "RETIRED": set(),
                }
                if (backend,lifecycle) not in valid[current["lifecycle"]]:
                    raise ValueError("Invalid migration lifecycle transition.")
                db.execute("""UPDATE vector_control SET epoch=epoch+1,backend=?,lifecycle=?,
                    activated_at=?,manifest=? WHERE singleton=1""",
                    (backend,lifecycle,time.time() if lifecycle=="CUTOVER_ACTIVE" else current["activated_at"],canonical(manifest)))


@contextmanager
def _borrow(value):
    yield value
