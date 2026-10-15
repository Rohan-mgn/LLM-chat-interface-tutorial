"""Rebuildable dedicated vector index; SQLite remains the source of authority.

Only this module imports LanceDB. No embedding functions or models are installed.
Search candidates intentionally omit text: callers must hydrate authorized sources.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
from typing import Protocol

from .storage_gate import ProcessGate

MAX_ROWS = 4000


class IndexUnavailable(ValueError):
    """An unavailable index is not an empty retrieval result."""


class IndexInconsistent(IndexUnavailable):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("Invalid internal index identifier.")
    return value


def integer(value, minimum=0):
    if type(value) is not int or not minimum <= value <= 2**63-1:
        raise ValueError("Invalid internal index number.")
    return value


def normalized(vector, dimensions):
    if len(vector) != dimensions or not 1 <= dimensions <= 8192:
        raise ValueError("Incompatible embedding dimensions.")
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in vector):
        raise ValueError("Invalid nonfinite embedding.")
    norm = math.hypot(*vector)
    if not math.isfinite(norm) or norm == 0:
        raise ValueError("Invalid embedding norm.")
    result = list(struct.unpack("<"+"f"*dimensions, struct.pack("<"+"f"*dimensions, *(x/norm for x in vector))))
    if not any(result):
        raise ValueError("Embedding underflow.")
    return result


@dataclass(frozen=True)
class EmbeddingSpace:
    model: str
    model_digest: str
    dimensions: int
    representation: str
    document_prefix: str
    query_prefix: str
    normalization: str = "unit-float32-v1"
    metric: str = "cosine"

    def __post_init__(self):
        integer(self.dimensions, 1)
        if self.dimensions > 8192 or not self.model or not self.model_digest or not self.representation:
            raise ValueError("Incomplete embedding-space identity.")
        if self.normalization != "unit-float32-v1" or self.metric != "cosine":
            raise ValueError("Unsupported embedding configuration.")

    @property
    def key(self):
        return digest(asdict(self))


@dataclass(frozen=True)
class GenerationScope:
    conversation_id: int
    file_id: str
    generation: int
    file_fingerprint: str

    def __post_init__(self):
        integer(self.conversation_id, 1)
        identifier(self.file_id)
        integer(self.generation)
        if not re.fullmatch(r"[a-f0-9]{64}", self.file_fingerprint):
            raise ValueError("Invalid file fingerprint.")


def source_fingerprint(text, metadata):
    return digest({"text": text, "metadata": metadata})


def row_identity(space, scope, source_id):
    return digest([space.key, asdict(scope), identifier(source_id)])


def scope_filter(space, scopes, source_ids=None):
    if not scopes or len(scopes) > 40 or len({s.conversation_id for s in scopes}) != 1:
        raise ValueError("A bounded single-conversation authorized scope is required.")
    clauses = ["(conversation_id = " + str(s.conversation_id) +
        " AND file_id = '" + identifier(s.file_id) + "' AND generation = " + str(s.generation) +
        " AND file_fingerprint = '" + s.file_fingerprint + "')" for s in scopes]
    expression = "space = '" + space.key + "' AND (" + " OR ".join(clauses) + ")"
    if source_ids is not None:
        if not source_ids or len(source_ids) > MAX_ROWS:
            raise ValueError("A bounded source restriction is required.")
        expression += " AND source_id IN (" + ",".join("'" + identifier(s) + "'" for s in source_ids) + ")"
    return expression


class VectorStorage(Protocol):
    def initialize_index(self, space): ...
    def upsert_chunks(self, space, scope, chunks): ...
    def search_vectors(self, space, scopes, vector, limit=30, source_ids=None): ...
    def search_text(self, space, scopes, query, limit=30, source_ids=None): ...
    def delete_document_vectors(self, space, scope): ...
    def delete_source_vectors(self, space, scope, source_ids): ...
    def verify_index_consistency(self, space, scope, manifest, *, verify_vectors=True): ...
    def get_index_status(self, space): ...
    def maintenance(self, space, **kwargs): ...
    def shutdown(self): ...


class LanceVectorStore:
    """Exact cosine; fresh read handles; process-coordinated readers and writers."""
    def __init__(self, directory):
        directory = Path(directory)
        if not directory.is_absolute():
            raise ValueError("Vector database directory must be absolute.")
        self.directory = directory.resolve()
        self.gate = ProcessGate(self.directory / ".coord" / "storage.lock")
        self.closed = False

    @classmethod
    def from_data_dir(cls, data_dir):
        return cls(os.environ.get("VECTOR_DB_DIR") or Path(data_dir).resolve() / "vectors" / "lancedb")

    def _connect(self):
        if self.closed:
            raise IndexUnavailable("Vector store has been shut down.")
        try:
            import lancedb
        except ImportError as error:
            raise IndexUnavailable("Install the validated Stage 9 vector requirements.") from error
        return lancedb.connect(str(self.directory), read_consistency_interval=timedelta(0))

    @staticmethod
    def _name(space):
        return "v_" + space.key[:24]  # Full identity is checked in schema and every row.

    def _table(self, space):
        try:
            return self._connect().open_table(self._name(space))
        except Exception as error:
            if isinstance(error, IndexUnavailable):
                raise
            raise IndexUnavailable("Vector index unavailable; repair or rebuild it.") from error

    def initialize_index(self, space):
        import pyarrow as pa
        schema = pa.schema([
            pa.field("row_id", pa.string()), pa.field("source_id", pa.string()),
            pa.field("conversation_id", pa.int64()), pa.field("file_id", pa.string()),
            pa.field("generation", pa.int64()), pa.field("file_fingerprint", pa.string()),
            pa.field("space", pa.string()), pa.field("source_fingerprint", pa.string()),
            pa.field("ordinal", pa.int64()), pa.field("metadata", pa.string()),
            pa.field("text", pa.string()), pa.field("vector", pa.list_(pa.float32(), space.dimensions)),
        ], metadata={b"embedding_space": canonical(asdict(space)).encode("utf-8")})
        with self.gate.hold():
            table = self._connect().create_table(self._name(space), schema=schema, exist_ok=True)
            if not table.schema.equals(schema, check_metadata=True):
                raise IndexInconsistent("Existing vector table has an incompatible schema.")

    def _rows(self, space, scope, chunks):
        if not chunks or len(chunks) > MAX_ROWS:
            raise ValueError("Generation must contain 1-4,000 chunks.")
        rows, seen = [], set()
        for chunk in chunks:
            sid = identifier(chunk["id"])
            if sid in seen:
                raise ValueError("Duplicate source in index generation.")
            seen.add(sid)
            text, metadata = chunk["text"], chunk["metadata"]
            if not isinstance(text, str) or not text or len(text.encode("utf-8")) > 256_000:
                raise ValueError("Invalid or oversized search representation.")
            rows.append(dict(row_id=row_identity(space, scope, sid), source_id=sid,
                **asdict(scope), space=space.key, source_fingerprint=source_fingerprint(text, metadata),
                ordinal=integer(chunk["chunk_index"]), metadata=canonical(metadata), text=text,
                vector=normalized(chunk["vector"], space.dimensions)))
        return rows

    @staticmethod
    def _manifest(rows):
        return {r["row_id"]: {"source_id": r["source_id"], "source_fingerprint": r["source_fingerprint"],
                "vector_fingerprint": hashlib.sha256(struct.pack("<"+"f"*len(r["vector"]), *r["vector"])).hexdigest()}
                for r in rows}

    def upsert_chunks(self, space, scope, chunks):
        import pyarrow as pa
        if not chunks or len(chunks)>MAX_ROWS:
            raise ValueError("Generation must contain 1-4,000 chunks.")
        ids=[identifier(chunk["id"]) for chunk in chunks]
        if len(set(ids))!=len(ids):
            raise ValueError("Duplicate source in index generation.")
        manifest={}
        with self.gate.hold():
            table=self._table(space)
            # Stream packed Arrow batches into ONE atomic merge. Do not retain a
            # second complete Python-float copy of the supplied embedding set.
            def batches():
                for start in range(0,len(chunks),128):
                    rows=self._rows(space,scope,chunks[start:start+128])
                    manifest.update(self._manifest(rows))
                    yield pa.RecordBatch.from_pylist(rows,schema=table.schema)
            reader=pa.RecordBatchReader.from_batches(table.schema,batches())
            try:
                table.merge_insert("row_id").when_matched_update_all().when_not_matched_insert_all().execute(
                    reader,timeout=timedelta(seconds=120))
            finally:
                reader.close()
            if len(manifest)!=len(chunks):
                raise IndexInconsistent("The native writer did not consume the complete generation.")
            self.verify_index_consistency(space,scope,manifest)
            return {"version":table.version,"manifest":manifest,"space":asdict(space)}

    def verify_index_consistency(self, space, scope, manifest, *, verify_vectors=True):
        if not manifest or len(manifest) > MAX_ROWS:
            raise IndexInconsistent("Missing or oversized generation manifest.")
        with self.gate.hold():
            table = self._table(space)
            query = table.search().where(scope_filter(space, [scope])).limit(MAX_ROWS+1)
            if not verify_vectors:
                rows=query.select(["row_id","source_id","source_fingerprint"]).to_list()
                expected={key:{"source_id":value["source_id"],"source_fingerprint":value["source_fingerprint"]}
                          for key,value in manifest.items()}
                actual={r["row_id"]:{"source_id":r["source_id"],"source_fingerprint":r["source_fingerprint"]} for r in rows}
                if len(rows)!=len(manifest) or actual!=expected:
                    raise IndexInconsistent("Published source manifest is incomplete or changed.")
                return {"count":len(rows),"version":table.version}
            actual={}
            count=0
            with query.to_batches(128,timeout=timedelta(seconds=120)) as batches:
                for batch in batches:
                    rows=batch.to_pylist()
                    count+=len(rows)
                    actual.update(self._manifest(rows))
                    for r in rows:
                        if (r["row_id"] != row_identity(space, scope, r["source_id"]) or
                            r["source_fingerprint"] != source_fingerprint(r["text"], json.loads(r["metadata"]))):
                            raise IndexInconsistent("Vector source mapping does not match its manifest.")
                        normalized(r["vector"],space.dimensions)
            if count!=len(manifest) or actual!=manifest:
                raise IndexInconsistent("Published vector generation is incomplete or changed.")
            return {"count":count,"version":table.version}

    def _search(self, space, scopes, query, limit, source_ids, text=False):
        integer(limit, 1)
        if limit > 100:
            raise ValueError("Candidate limit exceeded.")
        expression = scope_filter(space, scopes, source_ids)
        with self.gate.hold():
            table = self._table(space)
            try:
                search = table.search(query, query_type="fts") if text else table.search(normalized(query, space.dimensions)).distance_type("cosine").bypass_vector_index()
                fields=["row_id","source_id","conversation_id","file_id","generation",
                        "file_fingerprint","space","source_fingerprint","ordinal"]
                fields.append("_score" if text else "_distance")
                rows = search.where(expression, prefilter=True).select(fields).limit(limit).to_list()
            except Exception as error:
                raise IndexUnavailable("Vector search failed; authoritative document reading remains available.") from error
            return [{k: r[k] for k in ("row_id", "source_id", "conversation_id", "file_id", "generation",
                        "file_fingerprint", "space", "source_fingerprint", "ordinal")} |
                    {"score": float(r["_score"]) if text else 1-float(r["_distance"])} for r in rows]

    def search_vectors(self, space, scopes, vector, limit=30, source_ids=None):
        return self._search(space, scopes, vector, limit, source_ids)

    def search_text(self, space, scopes, query, limit=30, source_ids=None):
        if not isinstance(query, str) or not query.strip() or len(query) > 1000:
            raise ValueError("Invalid text query.")
        return self._search(space, scopes, query, limit, source_ids, text=True)

    def build_text_index(self, space):
        from lancedb.index import FTS
        with self.gate.hold():
            self._table(space).create_index("text",
                config=FTS(with_position=True, stem=False, remove_stop_words=False,
                           ascii_folding=False, max_token_length=128, num_workers=1), replace=True)

    def delete_document_vectors(self, space, scope):
        with self.gate.hold():
            table=self._table(space)
            expression=scope_filter(space,[scope])
            # Completed deletion tombstones are checked again after delayed writes.
            # Avoid creating another native table version when nothing remains.
            present=bool(table.search().where(expression).select(["row_id"]).limit(1).to_list())
            if present:
                table.delete(expression)
            return present

    def delete_source_vectors(self, space, scope, source_ids):
        with self.gate.hold():
            self._table(space).delete(scope_filter(space, [scope], source_ids))

    def recover_generation(self, space, scope, expected_sources):
        """Recover an acknowledged-or-not write, never fabricate missing rows."""
        with self.gate.hold():
            table=self._table(space)
            rows=table.search().where(scope_filter(space,[scope])).limit(MAX_ROWS+1).to_list()
            actual={r["source_id"]:r["source_fingerprint"] for r in rows}
            if len(rows)!=len(expected_sources) or actual!=expected_sources:
                raise IndexInconsistent("Interrupted generation is incomplete; compatible re-indexing is required.")
            manifest=self._manifest(rows)
            self.verify_index_consistency(space,scope,manifest)
            return {"version":table.version,"manifest":manifest,"space":asdict(space)}

    def export_generation(self, space, scope, manifest):
        """Explicit migration/recovery interface, never exposed to model tools."""
        with self.gate.hold():
            self.verify_index_consistency(space, scope, manifest)
            return self._table(space).search().where(scope_filter(space,[scope])).limit(MAX_ROWS).to_list()

    def get_index_status(self, space):
        with self.gate.hold():
            table = self._table(space)
            return {"rows": table.count_rows(), "version": table.version,
                "disk_bytes": sum(p.stat().st_size for p in self.directory.rglob("*") if p.is_file()),
                "indexes": [str(index) for index in table.list_indices()]}

    def maintenance(self, space, *, retention_days=14, protected_versions=(), allow_cleanup=False):
        if type(retention_days) is not int or retention_days < 14:
            raise ValueError("Recovery retention must be at least 14 days.")
        if allow_cleanup and protected_versions:
            raise ValueError("Protected snapshots prevent automatic version pruning.")
        with self.gate.hold():
            table = self._table(space)
            versions = table.list_versions()
            # Public optimize API couples compaction and pruning. With no explicit
            # cleanup decision, put its cutoff before EVERY existing version.
            age = timedelta(days=retention_days)
            if not allow_cleanup:
                oldest = min(v["timestamp"].timestamp() for v in versions)
                age = max(age, timedelta(seconds=max(0, datetime.now(timezone.utc).timestamp()-oldest)+86400))
            optimized = table.optimize(cleanup_older_than=age, delete_unverified=False)
            if not allow_cleanup:
                remaining = {v["version"] for v in table.list_versions()}
                if not {v["version"] for v in versions}.issubset(remaining):
                    raise IndexInconsistent("Maintenance unexpectedly removed a protected version.")
            return {"optimization": str(optimized), "cleanup_authorized": allow_cleanup, **self.get_index_status(space)}

    def shutdown(self):
        with self.gate.hold():
            self.closed = True
