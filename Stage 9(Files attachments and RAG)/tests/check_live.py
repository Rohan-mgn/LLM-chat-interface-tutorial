"""Optional live Ollama check with a real Uvicorn restart and an isolated SQLite DB."""

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import uuid

import httpx


STAGE = Path(__file__).resolve().parents[1]
ROOT = STAGE.parent

# Run the unchanged app in a fresh process, redirecting only its database to a
# unique test directory. Record the real model input to verify retained context.
SERVER_CODE = """
import importlib.util, json, sys
from pathlib import Path
import ollama, uvicorn
stage, run_dir = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(stage))
spec = importlib.util.spec_from_file_location('live_stage9', stage / 'main.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert module.DATABASE == stage.parent / 'data' / 'stage9' / 'chat.db'
module.DATA_DIR = run_dir / 'database'
module.DATABASE = module.DATA_DIR / 'chat.db'
class RecordingClient(ollama.AsyncClient):
    async def chat(self, **kwargs):
        (run_dir / 'model-input.json').write_text(json.dumps(kwargs), encoding='utf-8')
        return await super().chat(**kwargs)
module.ollama.AsyncClient = RecordingClient
uvicorn.run(module.app, host='127.0.0.1', port=int(sys.argv[3]), log_level='warning')
"""


def main():
    test_root = (STAGE / "tests" / ".artifacts").resolve()
    run_dir = test_root / ("stage9-live-" + uuid.uuid4().hex)
    run_dir.mkdir(parents=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    process = None
    log = (run_dir / "server.log").open("w", encoding="utf-8")

    def start_server():
        process = subprocess.Popen(
            [sys.executable, "-B", "-c", SERVER_CODE, str(STAGE), str(run_dir), str(port)],
            # Launch away from main.py to exercise stable file/static paths.
            cwd=STAGE / "static", stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError("Uvicorn failed to start")
                try:
                    if httpx.get(base_url + "/conversations", timeout=1).status_code == 200:
                        return process
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            raise RuntimeError("Uvicorn startup timed out")
        except BaseException:
            stop_server(process)
            raise

    def stop_server(process):
        if process is not None and process.poll() is None:
            if os.name == "nt":
                # The venv launcher can have a Python child holding the server
                # socket and log open; stop the isolated test tree together.
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               check=True, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)

    def send(client, conversation_id, message):
        started = time.monotonic()
        pieces = []
        timings = []
        with client.stream("POST", "/chat", json={"conversation_id": conversation_id, "message": message, "generation_id": str(uuid.uuid4())}) as response:
            response.raise_for_status()
            complete = False
            for line in response.iter_lines():
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[6:])
                if event["type"] == "delta":
                    pieces.append(event["content"])
                    timings.append(time.monotonic() - started)
                elif event["type"] == "done":
                    complete = event["status"] == "completed"
            assert complete, "Response was not marked complete"
        reply = "".join(pieces)
        assert reply.strip(), "Ollama returned an empty reply"
        print(json.dumps({"conversation_id": conversation_id, "question": message, "reply": reply, "chunks": len(pieces),
                          "first_chunk_seconds": round(timings[0], 3),
                          "last_chunk_seconds": round(timings[-1], 3)}), flush=True)
        return reply, timings

    try:
        process = start_server()
        with httpx.Client(base_url=base_url, timeout=150) as client:
            assert client.get("/").status_code == 200
            created = client.post("/conversations")
            created.raise_for_status()
            conversation_a = created.json()["id"]
            _, timings = send(client, conversation_a, "My secret test word is pineapple.")
            assert len(timings) > 1 and timings[-1] > timings[0], "No progressive output observed"
            answer, _ = send(client, conversation_a, "What is my secret test word?")
            assert "pineapple" in answer.lower(), "Model did not recall Conversation A's secret"
            saved_a = client.get(f"/conversations/{conversation_a}/messages").json()["messages"]
            assert len(saved_a) == 4

            created = client.post("/conversations")
            created.raise_for_status()
            conversation_b = created.json()["id"]
            answer, _ = send(client, conversation_b, "What is my secret test word?")
            assert "pineapple" not in answer.lower(), "Conversation A's secret appeared in B"
            model_input = json.loads((run_dir / "model-input.json").read_text(encoding="utf-8"))
            assert len(model_input["messages"]) == 2
            assert model_input["messages"][0]["role"] == "system"
            saved_b = client.get(f"/conversations/{conversation_b}/messages").json()["messages"]
            assert len(saved_b) == 2
            assert client.get(f"/conversations/{conversation_a}/messages").json()["messages"] == saved_a

        stop_server(process)
        process = start_server()
        with httpx.Client(base_url=base_url, timeout=150) as client:
            assert len(client.get("/conversations").json()["conversations"]) == 2
            assert client.get(f"/conversations/{conversation_a}/messages").json()["messages"] == saved_a
            assert client.get(f"/conversations/{conversation_b}/messages").json()["messages"] == saved_b
            question = "What is my secret word again?"
            answer, _ = send(client, conversation_a, question)
            assert "pineapple" in answer.lower(), "Model lost Conversation A's context after restart"
            model_input = json.loads((run_dir / "model-input.json").read_text(encoding="utf-8"))
            assert model_input["messages"][0]["role"] == "system"
            assert model_input["messages"][1:] == saved_a + [{"role": "user", "content": question}]
            assert len(client.get(f"/conversations/{conversation_a}/messages").json()["messages"]) == 6
            assert client.get(f"/conversations/{conversation_b}/messages").json()["messages"] == saved_b
        print("PASS: live streaming, pineapple A/B/A isolation, real Uvicorn restart, and both chats retained.", flush=True)
    except BaseException:
        log.flush()
        print((run_dir / "server.log").read_text(encoding="utf-8"), flush=True)
        raise
    finally:
        stop_server(process)
        log.close()
        assert run_dir.resolve().parent == test_root
        assert run_dir.name.startswith("stage9-live-")
        shutil.rmtree(run_dir)


if __name__ == "__main__":
    main()
