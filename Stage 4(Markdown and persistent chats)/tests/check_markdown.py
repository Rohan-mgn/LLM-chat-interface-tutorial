"""Run Stage 4 Markdown and persistence checks with Edge and the Python venv.

No Ollama request is made. The real page, libraries, CSS, and JavaScript are
served by FastAPI; model output is replaced with deterministic streamed text.
"""

import argparse
import asyncio
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from html.parser import HTMLParser
from unittest.mock import patch
import uuid

from fastapi.responses import FileResponse, HTMLResponse
from fastapi.testclient import TestClient
import uvicorn


STAGE = Path(__file__).resolve().parents[1]
TESTS = STAGE / "tests"


def load_app():
    spec = importlib.util.spec_from_file_location("stage4_main", STAGE / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_backend(module):
    with TestClient(module.app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert page.content == (STAGE / "static/index.html").read_bytes()
        for path in (
            "/static/style.css", "/static/script.js",
            "/static/vendor/marked-18.0.14.umd.js",
            "/static/vendor/dompurify-3.4.16.min.js",
        ):
            response = client.get(path)
            assert response.status_code == 200, path
            expected_type = "text/css" if path.endswith(".css") else "javascript"
            assert expected_type in response.headers["content-type"], path

        history = [{"role": "user", "content": "Show Markdown."}]
        chunks = [{"message": {"content": text}} for text in ("# Heading\n\n", "**Bold**")]
        with patch.object(module.ollama, "chat", side_effect=lambda **kwargs: iter(chunks)) as model:
            with contextlib.redirect_stdout(io.StringIO()):
                streamed = module.chat(module.ChatRequest(messages=history))

                async def collect_chunks():
                    return [chunk async for chunk in streamed.body_iterator]

                assert asyncio.run(collect_chunks()) == ["# Heading\n\n", "**Bold**"]
                response = client.post("/chat", json={"messages": history})
            assert response.text == "# Heading\n\n**Bold**"
            assert response.headers["content-type"].startswith("text/plain")
            arguments = model.call_args.kwargs
            assert arguments["stream"] is True
            assert arguments["messages"][0]["role"] == "system"
            assert "Markdown" in arguments["messages"][0]["content"]
            assert arguments["messages"][1:] == history
    print("PASS: static assets, updated system prompt, raw Markdown streaming, and history.")


class PageResult(HTMLParser):
    result = None

    def handle_starttag(self, tag, attrs):
        if tag == "body":
            value = dict(attrs).get("data-result")
            if value:
                self.result = json.loads(value)


def check_browser(base_url, browser, artifacts, name, path, size):
    result = subprocess.run([
        str(browser), "--headless", "--disable-gpu", "--no-first-run",
        "--no-default-browser-check", "--disable-background-networking",
        "--disable-extensions", "--force-device-scale-factor=1",
        "--user-data-dir=" + str(artifacts / (name + "-profile")),
        "--window-size=" + size, "--dump-dom", "--virtual-time-budget=6000",
        "--screenshot=" + str(artifacts / (name + ".png")), base_url + path,
    ], capture_output=True, text=True, encoding="utf-8", errors="replace",
       timeout=45, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    parsed = PageResult()
    parsed.feed(result.stdout)
    print(json.dumps({"browser": name, "details": parsed.result}))
    if not parsed.result or parsed.result.get("result") != "PASS":
        raise AssertionError(result.stderr[-2000:] + "\n" + str(parsed.result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", type=Path, default=Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"))
    parser.add_argument("--keep-artifacts", action="store_true", help="Keep screenshots and temporary browser profiles for inspection.")
    options = parser.parse_args()
    if not options.browser.is_file():
        parser.error("Pass --browser with the path to an installed Edge or Chromium browser.")

    module = load_app()
    check_backend(module)

    @module.app.get("/__markdown_check", response_class=HTMLResponse)
    def check_page():
        html = (STAGE / "static/index.html").read_text(encoding="utf-8")
        return html.replace("</body>", '<script src="/__markdown_checks.js"></script></body>')

    @module.app.get("/__markdown_checks.js")
    def check_script():
        return FileResponse(TESTS / "markdown_checks.js", media_type="text/javascript")

    @module.app.get("/__persistence_check", response_class=HTMLResponse)
    def persistence_page():
        html = (STAGE / "static/index.html").read_text(encoding="utf-8")
        return html.replace("</body>", '<script src="/__persistence_checks.js"></script></body>')

    @module.app.get("/__persistence_checks.js")
    def persistence_script():
        return FileResponse(TESTS / "persistence_checks.js", media_type="text/javascript")

    @module.app.get("/__mobile_check", response_class=HTMLResponse)
    def mobile_page():
        return """<!doctype html><html><head><meta charset="utf-8"></head>
        <body style="margin:0;background:#fcfcfa">
        <iframe title="Mobile Markdown test" src="/__markdown_check"
            style="border:0;width:390px;height:844px;display:block"></iframe>
        <script>window.addEventListener("message", event => {
            if (event.origin === location.origin && event.source !== window)
                document.body.dataset.result = JSON.stringify(event.data);
        });</script></body></html>"""

    temp_root = Path(tempfile.gettempdir()).resolve()
    artifacts = temp_root / ("stage4-markdown-" + uuid.uuid4().hex)
    artifacts.mkdir()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(module.app, log_level="error"))
    worker = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if not worker.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("The local test server did not start.")
            time.sleep(.05)
        base_url = f"http://127.0.0.1:{port}"
        check_browser(base_url, options.browser, artifacts, "desktop", "/__markdown_check", "1440,1000")
        check_browser(base_url, options.browser, artifacts, "mobile", "/__mobile_check", "500,1000")
        check_browser(base_url, options.browser, artifacts, "persistence", "/__persistence_check", "1440,1000")
    finally:
        server.should_exit = True
        worker.join(timeout=10)
        sock.close()
        if options.keep_artifacts:
            print("ARTIFACTS=" + str(artifacts))
        else:
            assert artifacts.resolve().parent == temp_root and artifacts.name.startswith("stage4-markdown-")
            shutil.rmtree(artifacts)


if __name__ == "__main__":
    main()
