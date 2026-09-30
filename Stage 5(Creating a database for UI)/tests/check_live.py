"""Optional live Ollama check with a real Uvicorn restart and an isolated SQLite DB."""

import json
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
spec = importlib.util.spec_from_file_location('live_stage5', stage / 'main.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert module.DATABASE == stage.parent / 'data' / 'chat.db'
module.DATA_DIR = run_dir / 'database'
module.DATABASE = module.DATA_DIR / 'chat.db'
client = ollama.Client(timeout=120)
def recorded_chat(**kwargs):
    (run_dir / 'model-input.json').write_text(json.dumps(kwargs), encoding='utf-8')
    return client.chat(**kwargs)
module.ollama.chat = recorded_chat
uvicorn.run(module.app, host='127.0.0.1', port=int(sys.argv[3]), log_level='warning')
"""


def main():
    test_root = (ROOT / "data").resolve()
    run_dir = test_root / ("lesson9-live-" + uuid.uuid4().hex)
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
        )
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError("Uvicorn failed to start")
                try:
                    if httpx.get(base_url + "/messages", timeout=1).status_code == 200:
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
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)

    def send(client, message):
        started = time.monotonic()
        pieces = []
        timings = []
        with client.stream("POST", "/chat", json={"message": message}) as response:
            response.raise_for_status()
            for piece in response.iter_text():
                pieces.append(piece)
                timings.append(time.monotonic() - started)
        reply = "".join(pieces)
        assert reply.strip(), "Ollama returned an empty reply"
        print(json.dumps({"question": message, "reply": reply, "chunks": len(pieces),
                          "first_chunk_seconds": round(timings[0], 3),
                          "last_chunk_seconds": round(timings[-1], 3)}), flush=True)
        return reply, timings

    try:
        process = start_server()
        with httpx.Client(base_url=base_url, timeout=150) as client:
            assert client.get("/").status_code == 200
            _, timings = send(client, "My favorite programming language is Python.")
            assert len(timings) > 1 and timings[-1] > timings[0], "No progressive output observed"
            answer, _ = send(client, "What programming language did I say I like? Answer in one sentence.")
            assert "python" in answer.lower(), "Model did not recall the preference"
            saved = client.get("/messages").json()["messages"]
            assert len(saved) == 4

        stop_server(process)
        process = start_server()
        with httpx.Client(base_url=base_url, timeout=150) as client:
            assert client.get("/messages").json()["messages"] == saved
            question = "Which language should you use for examples for me? Answer in one sentence."
            answer, _ = send(client, question)
            assert "python" in answer.lower(), "Model lost context after restart"
            model_input = json.loads((run_dir / "model-input.json").read_text(encoding="utf-8"))
            assert model_input["messages"][0]["role"] == "system"
            assert model_input["messages"][1:] == saved + [{"role": "user", "content": question}]
            assert client.delete("/messages").status_code == 200
            assert client.get("/messages").json() == {"messages": []}
            send(client, "Say hello in one short sentence.")
            model_input = json.loads((run_dir / "model-input.json").read_text(encoding="utf-8"))
            assert len(model_input["messages"]) == 2
            assert len(client.get("/messages").json()["messages"]) == 2
        print("PASS: live streaming, Python preference recall, real Uvicorn restart, retained history, and fresh chat.", flush=True)
    except BaseException:
        log.flush()
        print((run_dir / "server.log").read_text(encoding="utf-8"), flush=True)
        raise
    finally:
        stop_server(process)
        log.close()
        assert run_dir.resolve().parent == test_root
        assert run_dir.name.startswith("lesson9-live-")
        shutil.rmtree(run_dir)


if __name__ == "__main__":
    main()
