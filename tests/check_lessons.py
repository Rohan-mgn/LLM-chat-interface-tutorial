"""Check every lesson's backend and browser flow without requesting real LLM output."""

import argparse
import asyncio
import contextlib
import importlib.util
import io
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch
import uuid

from fastapi.responses import FileResponse, HTMLResponse
from fastapi.testclient import TestClient
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
STAGES = [
    ROOT / "Stage 1(Connecting a LLM to a web UI)",
    ROOT / "Stage 2(Giving LLM chat history,system prompt and behaviour control)",
    ROOT / "Stage 3(Adding streaming to the conversation and better UI)",
    ROOT / "Stage 4(Markdown and persistent chats)",
]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_backend(module, stage, number):
    html = stage / ("index.html" if number < 3 else "static/index.html")
    with TestClient(module.app) as client:
        page = client.get("/")
        assert page.status_code == 200 and page.content == html.read_bytes()
        if number >= 3:
            for asset in ("style.css", "script.js"):
                response = client.get("/static/" + asset)
                assert response.status_code == 200
                assert response.content == (stage / "static" / asset).read_bytes()
        history = [
            {"role": "user", "content": "My name is XYZ. I am building a weather dashboard."},
            {"role": "assistant", "content": "Hello XYZ!"},
            {"role": "user", "content": "What is my name and project?"},
        ]
        if number == 1:
            with patch.object(module.ollama, "chat", return_value={"message": {"content": "Hello!"}}) as model:
                response = client.post("/chat", json={"message": "Hello"})
                assert response.json() == {"reply": "Hello!"}
                assert model.call_args.kwargs["messages"] == [{"role": "user", "content": "Hello"}]
        elif number == 2:
            with patch.object(module.ollama, "chat", return_value={"message": {"content": "XYZ, weather dashboard."}}) as model:
                with contextlib.redirect_stdout(io.StringIO()):
                    response = client.post("/chat", json={"messages": history})
                assert response.json() == {"reply": "XYZ, weather dashboard."}
                arguments = model.call_args.kwargs
                assert arguments["messages"][0]["role"] == "system"
                assert "information about yourself" in arguments["messages"][0]["content"]
                assert arguments["messages"][1:] == history
        else:
            chunks = [{"message": {"content": text}} for text in ("Hello ", "", "XYZ!")]
            with patch.object(module.ollama, "chat", side_effect=lambda **kwargs: iter(chunks)) as model:
                with contextlib.redirect_stdout(io.StringIO()):
                    stream = module.chat(module.ChatRequest(messages=history))

                    async def collect():
                        return [chunk async for chunk in stream.body_iterator]

                    assert asyncio.run(collect()) == ["Hello ", "XYZ!"]
                    response = client.post("/chat", json={"messages": history})
                assert response.text == "Hello XYZ!"
                assert response.headers["content-type"].startswith("text/plain")
                assert model.call_args.kwargs["stream"] is True
                assert model.call_args.kwargs["messages"][1:] == history
    print(f"PASS: Stage {number} backend, page, assets, and chat contract.", flush=True)


@contextlib.contextmanager
def serve(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    worker = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if not worker.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("Test server did not start")
            time.sleep(.05)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        worker.join(timeout=10)
        sock.close()


def check_early_browser(helper, module, stage, number, browser, artifacts):
    html_path = stage / ("index.html" if number < 3 else "static/index.html")
    script = ROOT / "tests" / ("basic_chat_checks.js" if number < 3 else "stage3_ui_checks.js")

    @module.app.get("/__lesson_check", response_class=HTMLResponse)
    def test_page():
        html = html_path.read_text(encoding="utf-8").replace("<body>", f'<body data-stage="{number}">')
        return html.replace("</body>", '<script src="/__lesson_checks.js"></script></body>')

    @module.app.get("/__lesson_checks.js")
    def test_script():
        return FileResponse(script, media_type="text/javascript")

    @module.app.get("/__lesson_mobile", response_class=HTMLResponse)
    def test_mobile():
        return """<!doctype html><html><body style="margin:0">
        <iframe src="/__lesson_check" title="Mobile UI check" style="border:0;width:390px;height:844px"></iframe>
        <script>window.addEventListener('message', event => {
            if (event.origin === location.origin && event.source !== window)
                document.body.dataset.result = JSON.stringify(event.data);
        });</script></body></html>"""

    with serve(module.app) as base_url:
        helper.check_browser(base_url, browser, artifacts, f"stage{number}", "/__lesson_check", "1440,1000")
        if number == 3:
            helper.check_browser(base_url, browser, artifacts, "stage3-mobile", "/__lesson_mobile", "500,1000")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", type=Path, default=Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"))
    options = parser.parse_args()
    helper_path = STAGES[3] / "tests/check_markdown.py"
    helper = load(helper_path, "stage4_test_helpers")
    temp_root = Path(tempfile.gettempdir()).resolve()
    artifacts = temp_root / ("lesson-checks-" + uuid.uuid4().hex)
    artifacts.mkdir()
    try:
        for number, stage in enumerate(STAGES, start=1):
            module = load(stage / "main.py", f"stage{number}_audit")
            check_backend(module, stage, number)
            if number < 4:
                check_early_browser(helper, module, stage, number, options.browser, artifacts)
        subprocess.run([sys.executable, "-B", str(helper_path), "--browser", str(options.browser)], check=True)
    finally:
        assert artifacts.resolve().parent == temp_root and artifacts.name.startswith("lesson-checks-")
        shutil.rmtree(artifacts)
    print("PASS: all four stages and their assigned lesson features.")


if __name__ == "__main__":
    main()
