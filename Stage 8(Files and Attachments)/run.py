"""Start Stage 8 with reload limited to the files the application uses."""

import argparse
from importlib.util import find_spec
from pathlib import Path
import subprocess
import sys


BASE_DIR = Path(__file__).resolve().parent

# Explicit dependencies keep tests, documentation, database writes, and other
# stages from restarting this app. Add new runtime files here when adding them
# to the application, then restart this launcher to pick up the changed list.
RUNTIME_FILES = (
    "main.py",
    "tree_store.py",
    "attachments.py",
    "upload_routes.py",
    "static/attachments.js",
    "static/index.html",
    "static/style.css",
    "static/script.js",
    "static/composer.js",
    "static/vendor/marked-18.0.14.umd.js",
    "static/vendor/dompurify-3.4.16.min.js",
)


WATCHED_PATHS = frozenset((BASE_DIR / file).resolve() for file in RUNTIME_FILES)


def application_file_changed(change, path):
    return Path(path).resolve() in WATCHED_PATHS


def report_changes(changes):
    names = sorted(str(Path(path).resolve().relative_to(BASE_DIR)) for _, path in changes)
    print("WARNING: Changes detected in " + ", ".join(names) + ". Restarting server...", flush=True)


def serve(port):
    import uvicorn

    # Ensure 'main' resolves to this stage, regardless of the terminal directory.
    sys.path.insert(0, str(BASE_DIR))
    # watchfiles owns the restart; don't start a second Uvicorn reloader.
    uvicorn.run("main:app", host="127.0.0.1", port=port)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8002)
    options = parser.parse_args()
    dependencies = ("uvicorn", "watchfiles", "fastapi", "ollama", "python_multipart", "PIL")
    missing = [name for name in dependencies if find_spec(name) is None]
    if missing:
        # IDE Run buttons may select system Python rather than the project's
        # environment. Relaunch with the existing venv before importing the app.
        python_relative_path = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
        project_python = BASE_DIR.parent / ".venv" / python_relative_path
        if project_python.is_file() and Path(sys.prefix).resolve() != project_python.parent.parent.resolve():
            print(f"Using project Python: {project_python}", flush=True)
            try:
                result = subprocess.run([str(project_python), str(Path(__file__).resolve()), *sys.argv[1:]])
            except KeyboardInterrupt:
                # Console Ctrl+C also reaches the child, which owns shutdown.
                raise SystemExit(130) from None
            raise SystemExit(result.returncode)

        requirements = BASE_DIR / "requirements-dev.txt"
        parser.error(
            f"Missing dependencies: {', '.join(missing)}. Install them with: "
            f'uv pip install --python "{sys.executable}" -r "{requirements}"'
        )

    from watchfiles import run_process

    print(f"Watching Stage 8 application files in {BASE_DIR}", flush=True)
    # Use watchfiles' process runner directly. This also avoids Uvicorn's
    # console-dependent Windows reload signal, which can leave a child running.
    run_process(
        BASE_DIR,
        target=serve,
        args=(options.port,),
        watch_filter=application_file_changed,
        callback=report_changes,
    )


if __name__ == "__main__":
    main()
