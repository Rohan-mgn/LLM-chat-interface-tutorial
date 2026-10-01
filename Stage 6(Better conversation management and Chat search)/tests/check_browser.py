"""Check Lesson 10 in Edge against real FastAPI/SQLite and a mocked Ollama.

All test databases and browser artifacts stay in an isolated directory under
the project's data folder. The production database is never opened or changed.
"""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
from threading import Event
import time
from unittest.mock import patch
import uuid

from fastapi.responses import FileResponse, HTMLResponse


STAGE = Path(__file__).resolve().parents[1]
ROOT = STAGE.parent
TESTS = STAGE / "tests"
PREFIX = "# Markdown demo\n\n**Bold text** and *emphasis*.\n\n"
SAMPLE = PREFIX + """## Lists

- First item
- Second item

1. First step
2. Second step

## Comparison

| Feature | Status |
| --- | --- |
| Streaming | Works |
| Markdown | Works |

## Code

Use `print()` to display text.

```python
print("Hello, XYZ!")
html = "<script>alert(1)</script>"
```

> A useful note.

[Documentation](https://example.com)

Unicode: café 👋

<img src=x onerror="window.__markdownXss=true">
<a href="javascript:alert(1)" onclick="window.__markdownXss=true">Unsafe link</a>
"""


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", type=Path, default=Path(
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"))
    parser.add_argument("--keep-artifacts", action="store_true")
    options = parser.parse_args()
    if not options.browser.is_file():
        parser.error("Pass --browser with the path to Edge or Chromium.")

    lessons = load(ROOT / "tests/check_lessons.py", "lesson_helpers")
    module = load(STAGE / "main.py", "stage5_browser_app")
    test_root = (ROOT / "data").resolve()
    run_dir = test_root / ("lesson10-browser-" + uuid.uuid4().hex)
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True)
    module.DATA_DIR = run_dir / "database"
    module.DATABASE = module.DATA_DIR / "chat.db"
    model_calls = []
    release_stream = Event()
    browser_finished = Event()
    browser_results = []

    def model_reply(**kwargs):
        assert kwargs["model"] == module.MODEL and kwargs["stream"] is True
        model_calls.append(kwargs)
        newest = kwargs["messages"][-1]["content"]
        if newest.startswith("Show Markdown"):
            yield {"message": {"content": PREFIX}}
            # A real HTTP request from the browser releases this stream only
            # after checking the partial DOM and the still-incomplete DB turn.
            if not release_stream.wait(timeout=20):
                raise RuntimeError("The browser did not release the test stream.")
            for offset in range(len(PREFIX), len(SAMPLE), 17):
                yield {"message": {"content": SAMPLE[offset:offset + 17]}}
        elif "secret" in newest.lower():
            knows_secret = any("pineapple" in item["content"].lower()
                               for item in kwargs["messages"] if item["role"] == "user")
            reply = "Your secret word is **pineapple**." if knows_secret else "You have not told me a secret word in this conversation."
            yield {"message": {"content": reply}}
        else:
            yield {"message": {"content": "# Fresh\n\nA fresh conversation."}}

    class TitleClient:
        def chat(self, **kwargs):
            if "User: Title test" in kwargs["messages"][1]["content"]:
                time.sleep(.3)
                return {"message": {"content": "Planning a Garden"}}
            raise RuntimeError("Controlled title failure: keep the fallback")

    @module.app.get("/__lesson10", response_class=HTMLResponse)
    def harness():
        return """<!doctype html><html><head><meta charset="utf-8"></head>
        <body style="margin:0;background:#fcfcfa">
        <iframe id="appFrame" title="Lesson 10 application"
            style="display:block;border:0;height:900px"></iframe>
        <script src="/__lesson10_checks.js"></script></body></html>"""

    @module.app.get("/__lesson10_checks.js")
    def browser_script():
        return FileResponse(TESTS / "browser_checks.js", media_type="text/javascript")

    @module.app.get("/__lesson10_state")
    def test_state():
        conversations = module.load_conversations()
        return {"conversations": conversations,
                "messages": {str(item["id"]): module.load_messages(item["id"]) for item in conversations},
                "model_calls": model_calls, "sample": SAMPLE}

    @module.app.post("/__lesson10_release")
    def finish_generation():
        release_stream.set()
        return {"released": True}

    @module.app.post("/__lesson10_result")
    def browser_result(result: dict):
        browser_results.append(result)
        browser_finished.set()
        return {"received": True}

    def check_browser(base_url, name, path, size):
        # Real streaming requests need wall-clock time. Chromium virtual-time
        # budgets can expire before an iframe's network operations finish.
        browser_finished.clear()
        browser_results.clear()
        log_path = artifacts / (name + ".log")
        with log_path.open("w", encoding="utf-8") as browser_log:
            process = subprocess.Popen([
                str(options.browser), "--headless", "--disable-gpu", "--no-first-run",
                "--no-default-browser-check", "--disable-background-networking",
                "--disable-extensions", "--force-device-scale-factor=1",
                "--user-data-dir=" + str(artifacts / (name + "-profile")),
                "--window-size=" + size, base_url + path,
            ], stdout=subprocess.DEVNULL, stderr=browser_log,
               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            try:
                if not browser_finished.wait(timeout=45):
                    raise AssertionError(f"{name}: browser did not report completion; see {log_path}")
                result = browser_results[-1]
                print(json.dumps({"browser": name, "details": result}), flush=True)
                assert result.get("result") == "PASS", result
            finally:
                release_stream.set()
                if process.poll() is None:
                    # Stop only the isolated browser process tree we started.
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       check=False, creationflags=subprocess.CREATE_NO_WINDOW)
                    else:
                        process.terminate()
                    process.wait(timeout=10)

    try:
        with patch.object(module.ollama, "chat", side_effect=model_reply), patch.object(module.ollama, "Client", return_value=TitleClient()):
            with lessons.serve(module.app) as base_url:
                for name, path, size in (
                    ("desktop", "/__lesson10", "1440,1000"),
                    ("mobile", "/__lesson10?mobile=1", "500,1000"),
                ):
                    from contextlib import closing
                    with closing(module.get_connection()) as connection, connection:
                        connection.execute("DELETE FROM conversations")
                    model_calls.clear()
                    release_stream.clear()
                    check_browser(base_url, name, path, size)
                    assert len(module.load_conversations()) == 0
                    assert len(model_calls[-1]["messages"]) == 2
        print("PASS: Lesson 10 multi-chat browser/API/SQLite integration on desktop and mobile.")
    finally:
        release_stream.set()
        if options.keep_artifacts:
            print("ARTIFACTS=" + str(run_dir))
        else:
            assert run_dir.resolve().parent == test_root
            assert run_dir.name.startswith("lesson10-browser-")
            # Windows may release browser-profile handles just after exit.
            for attempt in range(10):
                try:
                    shutil.rmtree(run_dir)
                    break
                except PermissionError:
                    if attempt == 9:
                        raise
                    time.sleep(.2)


if __name__ == "__main__":
    main()
