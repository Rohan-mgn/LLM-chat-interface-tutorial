"""Request-local measurements and reusable inputs; never shared chat answers."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import time

current = ContextVar("rag_request", default=None)
purpose = ContextVar("model_purpose", default="generation")

@dataclass
class RequestState:
    started: float = field(default_factory=time.perf_counter)
    calls: dict = field(default_factory=dict)
    timings: dict = field(default_factory=dict)
    metadata: object = None
    embeddings: dict = field(default_factory=dict)
    retrieval: dict = field(default_factory=dict)
    first_token_ms: float | None = None

    def call(self, kind):
        self.calls[kind] = self.calls.get(kind, 0) + 1

    def add_time(self, key, seconds):
        self.timings[key] = self.timings.get(key, 0) + seconds * 1000

    def report(self):
        return {"model_calls": dict(self.calls), "model_call_count": sum(v for k,v in self.calls.items() if k != "metadata"),
                "timings_ms": {k:round(v,2) for k,v in self.timings.items()},
                "total_ms": round((time.perf_counter()-self.started)*1000,2),
                "time_to_first_token_ms": self.first_token_ms, **self.retrieval}

@contextmanager
def phase(name):
    token=purpose.set(name)
    started=time.perf_counter()
    try: yield
    finally:
        if current.get(): current.get().add_time(name, time.perf_counter()-started)
        purpose.reset(token)

async def model_metadata(client, refresh=False):
    state=current.get()
    if state is not None and state.metadata is not None and not refresh:
        return state.metadata
    response=await client.list()
    snapshot={m["model"]:m["digest"] for m in response["models"]}
    if state is not None: state.metadata=snapshot
    return snapshot

async def model_digest(provider, model, refresh=False):
    client=provider.client(15)
    try:
        snapshot=await model_metadata(client,refresh)
        return snapshot.get(model) or snapshot.get(model+":latest")
    except (AttributeError, OSError, ValueError, KeyError):
        return None
    finally: await client.close()
