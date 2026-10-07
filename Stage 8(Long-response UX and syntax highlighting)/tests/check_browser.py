"""Check Lessons 13–14 in Edge against real FastAPI/SQLite and a mocked Ollama.

All test databases and browser artifacts stay in an isolated directory under
the project's data folder. The production database is never opened or changed.
"""

import argparse
import asyncio
import importlib.util
import sys
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
sys.path.insert(0, str(STAGE))
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
    parser.add_argument("--responses", action="store_true", help="Run Lesson 15?16 response UI checks")
    parser.add_argument("--composer-buttons", action="store_true", help="Check Indent and Unindent buttons")
    options = parser.parse_args()
    if not options.browser.is_file():
        parser.error("Pass --browser with the path to Edge or Chromium.")

    lessons = load(ROOT / "tests/check_lessons.py", "lesson_helpers")
    module = load(STAGE / "main.py", "stage8_browser_app")
    test_root = (ROOT / "data").resolve()
    run_dir = test_root / ("stage8-browser-" + uuid.uuid4().hex)
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True)
    module.DATA_DIR = run_dir / "database"
    module.DATABASE = module.DATA_DIR / "chat.db"
    model_calls = []
    release_stream = Event()
    browser_finished = Event()
    browser_results = []

    failures = set()
    class ChatClient:
        async def chat(self, **kwargs):
            model_calls.append(kwargs)
            newest = kwargs["messages"][-1]["content"]
            async def stream():
                if newest.startswith("Long response test"):
                    prefix = "## Long response\n\n\x60\x60\x60python\n" + "\n".join("stream_line_" + str(i) + " = " + repr("x" * 160) for i in range(80))
                    yield {"message": {"content": prefix}}
                    while not release_stream.is_set():
                        await asyncio.sleep(.02)
                    yield {"message": {"content": "\n\x60\x60\x60\n\n**Done.**"}}
                    return
                if newest.startswith("Wait before tokens"):
                    while not release_stream.is_set():
                        await asyncio.sleep(.02)
                if newest.startswith("Fail once") and newest not in failures:
                    failures.add(newest)
                    yield {"message": {"content": "A partial reply"}}
                    raise RuntimeError("Controlled failure")
                if newest.startswith("Stop after partial"):
                    yield {"message": {"content": "## Partial reply"}}
                    while not release_stream.is_set():
                        await asyncio.sleep(.02)
                if "secret" in newest.lower():
                    knows = any("pineapple" in m["content"] for m in kwargs["messages"] if m["role"] == "user")
                    reply = "Your word is **pineapple**." if knows else "No secret in this conversation."
                else:
                    reply = SAMPLE
                for offset in range(0, len(reply), 65):
                    yield {"message": {"content": reply[offset:offset + 65]}}
                    await asyncio.sleep(.005)
            return stream()
        async def close(self):
            pass

    class TitleClient:
        def chat(self, **kwargs):
            if "User: Title test" in kwargs["messages"][1]["content"]:
                time.sleep(.3)
                return {"message": {"content": "Planning a Garden"}}
            raise RuntimeError("Controlled title failure: keep the fallback")

    @module.app.get("/__stage8", response_class=HTMLResponse)
    def harness():
        return """<!doctype html><html><head><meta charset="utf-8"></head>
        <body style="margin:0;background:#fcfcfa">
        <iframe id="appFrame" title="Stage 8 application"
            style="display:block;border:0;height:900px"></iframe>
        <script src="/__stage8_checks.js"></script></body></html>"""

    @module.app.get("/__stage8_checks.js")
    def browser_script():
        return FileResponse(TESTS / ("composer_buttons.js" if options.composer_buttons else "response_checks.js" if options.responses else "browser_checks.js"), media_type="text/javascript")

    @module.app.get("/__stage8_slow", response_class=HTMLResponse)
    def slow_startup():
        # Delay metadata only inside this test page, including cancellation.
        fixture = """<script>
        const originalFetch = window.fetch.bind(window);
        const delayMessages = new URLSearchParams(location.search).has('messages');
        window.fetch = (url, options = {}) => {
            if (url === '/conversations' && delayMessages) {
                return Promise.resolve(new Response(JSON.stringify({conversations: [{
                    id: 9999, title: 'Saved chat', updated_at: '2026-10-03 00:00:00', title_source: 'manual'
                }]}), {status: 200}));
            }
            if (url === '/conversations' || String(url).endsWith('/tree')) {
                return new Promise((resolve, reject) => {
                    window.releaseHistory = () => resolve(new Response(JSON.stringify(
                        delayMessages ? {messages: [], active_children: {}} : {conversations: []}
                    ), {status: 200}));
                    options.signal.addEventListener('abort', () => reject(new DOMException('Timed out', 'AbortError')), {once: true});
                });
            }
            return originalFetch(url, options);
        };
        </script>"""
        html = (STAGE / "static/index.html").read_text(encoding="utf-8")
        return html.replace('<script src="/static/script.js">', fixture + '<script src="/static/script.js">')

    @module.app.get("/__stage8_state")
    def test_state():
        conversations = module.load_conversations()
        return {"conversations": conversations,
                "messages": {str(item["id"]): module.load_messages(item["id"], include_ids=True) for item in conversations},
                "trees": {str(item["id"]): module.store.tree(item["id"]) for item in conversations},
                "model_calls": model_calls, "sample": SAMPLE}

    @module.app.post("/__stage8_hold")
    def hold_generation():
        release_stream.clear()
        return {"held": True}

    @module.app.post("/__stage8_release")
    def finish_generation():
        release_stream.set()
        return {"released": True}

    @module.app.post("/__stage8_result")
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
                str(options.browser), "--headless", "--inprivate", "--disable-gpu", "--no-first-run",
                "--no-default-browser-check", "--disable-background-networking",
                "--disable-extensions", "--force-device-scale-factor=1",
                "--enable-logging=stderr",
                "--user-data-dir=" + str(artifacts / (name + "-profile")),
                "--window-size=" + size, base_url + path,
            ], stdout=subprocess.DEVNULL, stderr=browser_log,
               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            try:
                if not browser_finished.wait(timeout=90):
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
        with patch.object(module.ollama, "AsyncClient", side_effect=lambda **_: ChatClient()), patch.object(module.ollama, "Client", return_value=TitleClient()):
            with lessons.serve(module.app) as base_url:
                for name, path, size in (
                    ("desktop", "/__stage8", "1440,1000"),
                    ("mobile", "/__stage8?mobile=1", "500,1000"),
                ):
                    from contextlib import closing
                    with closing(module.get_connection()) as connection, connection:
                        connection.execute("DELETE FROM conversations")
                    model_calls.clear()
                    release_stream.clear()
                    check_browser(base_url, name, path, size)
                    assert len(module.load_conversations()) == 0
                    failures.clear()
        print("PASS: " + ("Indent/Unindent buttons" if options.composer_buttons else "Lessons 15-16 response UI" if options.responses else "Lessons 13-14 browser/API/SQLite regression") + " on desktop and mobile.")
    finally:
        release_stream.set()
        if options.keep_artifacts:
            print("ARTIFACTS=" + str(run_dir))
        else:
            assert run_dir.resolve().parent == test_root
            assert run_dir.name.startswith("stage8-browser-")
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
