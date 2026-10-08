"""Verify reload includes/excludes and actual server restarts in an isolated app."""

import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from contextlib import redirect_stderr
from unittest.mock import patch, Mock

STAGE = Path(__file__).resolve().parents[1]
ROOT = STAGE.parent


def main():
    spec = importlib.util.spec_from_file_location("stage9_launcher", STAGE / "run.py")
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    # System Python should hand off to the project's environment and preserve
    # CLI arguments/exit status. An incomplete venv must not relaunch itself.
    with patch.object(launcher, "find_spec", return_value=None), \
         patch.object(launcher.sys, "argv", [str(STAGE / "run.py"), "--port", "8123"]), \
         patch.object(launcher.sys, "prefix", str(ROOT)), \
         patch.object(launcher.subprocess, "run", return_value=Mock(returncode=7)) as child:
        try:
            launcher.main()
            raise AssertionError("Launcher did not propagate the child exit status")
        except SystemExit as error:
            assert error.code == 7
        command = child.call_args.args[0]
        assert Path(command[0]).parent.parent == ROOT / ".venv"
        assert command[1:] == [str(STAGE / "run.py"), "--port", "8123"]
    diagnostic = io.StringIO()
    with patch.object(launcher, "find_spec", return_value=None), \
         patch.object(launcher.sys, "argv", [str(STAGE / "run.py")]), \
         patch.object(launcher.sys, "prefix", str(ROOT / ".venv")), \
         patch.object(launcher.subprocess, "run") as child, redirect_stderr(diagnostic):
        try:
            launcher.main()
            raise AssertionError("Missing dependencies were not reported")
        except SystemExit as error:
            assert error.code == 2
        child.assert_not_called()
        assert "Missing dependencies: uvicorn" in diagnostic.getvalue()
        assert "requirements-dev.txt" in diagnostic.getvalue()
    print("PASS: project environment fallback, CLI forwarding, and missing-dependency diagnostics.", flush=True)
    expected = {STAGE / file for file in launcher.RUNTIME_FILES}
    ignored = [
        STAGE / "LESSON_9_PROMPT.md", STAGE / "run.py", STAGE / "requirements-dev.txt",
        STAGE / "tests/check_database.py", STAGE / "tests/browser_checks.js",
        STAGE / "tests/main.py", STAGE / "__pycache__/main.cpython-312.pyc",
        STAGE / "static/vendor/README.md", STAGE / "static/vendor/versions.json",
        STAGE / "static/vendor/marked-LICENSE", STAGE / "static/unused.js",
        ROOT / "README.md", ROOT / "data/chat.db", ROOT / "another-stage/main.py",
    ]
    previous_cwd = Path.cwd()
    try:
        for directory in (ROOT, STAGE, STAGE / "static"):
            os.chdir(directory)
            assert all(launcher.application_file_changed(2, str(path)) for path in expected)
            assert not any(launcher.application_file_changed(2, str(path)) for path in ignored)
    finally:
        os.chdir(previous_cwd)
    print("PASS: only actual runtime files match; launch directory does not affect the scope.", flush=True)

    run_dir = STAGE / "tests" / ".artifacts" / ("reload-check-" + uuid.uuid4().hex)
    app_dir = run_dir / STAGE.name
    app_dir.mkdir(parents=True)
    shutil.copy2(STAGE / "run.py", app_dir / "run.py")
    for file in launcher.RUNTIME_FILES:
        path = app_dir / file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    # Test the real launcher with a tiny app: no Ollama calls or real chat DB.
    (app_dir / "main.py").write_text(
        "import os\nfrom fastapi import FastAPI\napp = FastAPI()\n"
        "@app.get('/')\ndef home():\n    return {'pid': os.getpid()}\n",
        encoding="utf-8",
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}/"

    def server_pid():
        try:
            with urllib.request.urlopen(url, timeout=.5) as response:
                return json.load(response)["pid"]
        except (OSError, urllib.error.URLError):
            return None

    def wait_for_server(previous=None):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            pid = server_pid()
            if pid is not None and pid != previous:
                return pid
            if process.poll() is not None:
                raise AssertionError("Reload launcher exited unexpectedly")
            time.sleep(.1)
        raise AssertionError("Server did not start/restart in time")

    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    process = None
    log_path = run_dir / "server.log"
    try:
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [sys.executable, "-B", str(app_dir / "run.py"), "--port", str(port)],
                cwd=run_dir, stdout=log, stderr=subprocess.STDOUT, **options,
            )
            try:
                pid = wait_for_server()
                for file in launcher.RUNTIME_FILES:
                    # Let the watcher return to its next change batch.
                    time.sleep(.6)
                    path = app_dir / file
                    print("Checking actual restart for " + file, flush=True)
                    with path.open("a", encoding="utf-8") as handle:
                        handle.write("\n")
                    pid = wait_for_server(pid)
                    print("PASS: server restarted for " + file, flush=True)

                for path in ignored:
                    destination = run_dir / path.relative_to(ROOT)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    # Do not alter the running fixture's launcher itself.
                    if destination != app_dir / "run.py":
                        destination.write_text("test-only change\n", encoding="utf-8")
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    assert server_pid() == pid, "An unrelated change restarted the server"
                    time.sleep(.1)
                print("PASS: docs, tests, data, metadata, unused assets, and other stages do not restart the server.", flush=True)
            finally:
                if process.poll() is None:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       check=False, creationflags=subprocess.CREATE_NO_WINDOW)
                    else:
                        process.terminate()
                    process.wait(timeout=10)
    except BaseException:
        lines = [line for line in log_path.read_text(encoding="utf-8").splitlines()
                 if '"GET / HTTP/1.1" 200 OK' not in line]
        print("\n".join(lines)[-6000:], flush=True)
        raise
    finally:
        assert run_dir.resolve().parent == (STAGE / "tests" / ".artifacts").resolve()
        assert run_dir.name.startswith("reload-check-")
        shutil.rmtree(run_dir)


if __name__ == "__main__":
    main()
