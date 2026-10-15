"""Short cross-process reservations. Never hold across model inference or network I/O."""
from contextlib import contextmanager
import os
from pathlib import Path
import threading
import time
import weakref

class _GateState:
    def __init__(self):
        self.lock = threading.RLock()
        self.local = threading.local()
    def __iter__(self):
        return iter((self.lock, self.local))


_registry = weakref.WeakValueDictionary()
_registry_lock = threading.Lock()


class StorageBusy(RuntimeError):
    pass


class ProcessGate:
    """OS-backed lock plus thread coordination, reentrant within a thread."""
    def __init__(self, path, timeout=30):
        self.path = Path(path).resolve()
        self.timeout = timeout
        with _registry_lock:
            key = str(self.path).casefold()
            self.state = _registry.get(key)
            if self.state is None:
                self.state = _GateState()
                _registry[key] = self.state

    @contextmanager
    def hold(self):
        lock, local = self.state
        deadline = time.monotonic() + self.timeout
        if not lock.acquire(timeout=self.timeout):
            raise StorageBusy("Storage is busy; retry shortly.")
        handle = None
        try:
            if getattr(local, "depth", 0):
                local.depth += 1
                try:
                    yield
                finally:
                    local.depth -= 1
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(self.path, "a+b")
            if self.path.stat().st_size == 0:
                handle.write(b"0")
                handle.flush()
            while True:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except (OSError, BlockingIOError):
                    if time.monotonic() >= deadline:
                        raise StorageBusy("Storage reservation timed out.")
                    time.sleep(.02)
            local.depth = 1
            try:
                yield
            finally:
                local.depth = 0
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            if handle is not None:
                handle.close()
            lock.release()
