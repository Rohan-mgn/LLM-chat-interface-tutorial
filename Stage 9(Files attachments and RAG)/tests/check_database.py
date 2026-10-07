"""Stage 9 API, SQLite, branching and cancellation regression tests. No real model calls."""
import asyncio
from contextlib import closing
import importlib.util
import sys
from pathlib import Path
import json
import shutil
import sqlite3
import unittest
from unittest.mock import patch, Mock
from uuid import uuid4, UUID

from fastapi.testclient import TestClient

STAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STAGE))


class Model:
    def __init__(self, owner):
        self.owner = owner
        self.closed = False

    async def chat(self, **kwargs):
        self.owner.calls.append(kwargs)
        async def stream():
            if self.owner.mode == "upfront_error":
                raise RuntimeError("Unavailable")
            if self.owner.mode == "waiting":
                await asyncio.sleep(60)
            yield {"message": {"content": "**Hello** "}}
            if self.owner.mode == "partial_error":
                raise RuntimeError("Broken stream")
            if self.owner.mode == "slow":
                await asyncio.sleep(60)
            yield {"message": {"content": "world"}}
        return stream()

    async def close(self):
        self.closed = True


class Checks(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("stage9_" + uuid4().hex, STAGE / "main.py")
        self.app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.app)
        self.root = STAGE.parent / "data" / ("stage9-check-" + uuid4().hex)
        self.app.DATA_DIR, self.app.DATABASE = self.root, self.root / "chat.db"
        self.calls, self.clients, self.mode = [], [], "normal"
        def model(**kwargs):
            client = Model(self)
            self.clients.append(client)
            return client
        self.patches = [patch.object(self.app.ollama, "AsyncClient", side_effect=model),
                        patch.object(self.app.ollama, "Client", side_effect=RuntimeError("Fallback title"))]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.client = TestClient(self.app.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        assert self.root.resolve().parent == (STAGE.parent / "data").resolve()
        assert self.root.name.startswith("stage9-check-")
        shutil.rmtree(self.root)

    def create(self):
        response = self.client.post("/conversations")
        self.assertEqual(response.status_code, 201)
        return response.json()["id"]

    def messages(self, cid):
        return self.client.get(f"/conversations/{cid}/messages?include_ids=true").json()["messages"]

    def send(self, cid, message="Hello", action="send", message_id=None, generation_id=None):
        return self.client.post("/chat", json={"conversation_id": cid, "message": message, "action": action,
            "message_id": message_id, "generation_id": generation_id or str(uuid4())})

    def events(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        return [json.loads(packet[6:]) for packet in response.text.strip().split("\n\n")]

    def test_stream_protocol_persistence_and_model_context(self):
        cid = self.create()
        events = self.events(self.send(cid, "  keep indentation\n    code"))
        self.assertEqual([e["type"] for e in events], ["start", "delta", "delta", "done"])
        self.assertEqual(events[-1]["status"], "completed")
        rows = self.messages(cid)
        self.assertEqual(rows[0]["content"], "  keep indentation\n    code")
        self.assertEqual(rows[1]["content"], "**Hello** world")
        self.assertEqual(rows[1]["reply_to_message_id"], rows[0]["id"])
        self.assertEqual(rows[1]["id"], events[0]["assistant_message_id"])
        self.assertEqual(self.calls[0]["messages"][0]["role"], "system")
        self.assertTrue(all(set(m) == {"role", "content"} for m in self.calls[0]["messages"]))
        self.assertTrue(self.clients[0].closed)

    def test_conversation_isolation_followup_and_ordering(self):
        a, b = self.create(), self.create()
        self.send(a, "pineapple")
        self.send(b, "Separate chat")
        self.assertNotIn("pineapple", str(self.calls[-1]))
        self.send(a, "Recall")
        self.assertIn("pineapple", str(self.calls[-1]))
        self.assertEqual(len(self.messages(b)), 2)
        self.assertEqual(self.client.get("/conversations").json()["conversations"][0]["id"], a)

    def tree(self, cid):
        return self.client.get(f"/conversations/{cid}/tree").json()

    def select(self, cid, mid):
        response = self.client.put(f"/conversations/{cid}/branch", json={"message_id": mid})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_edit_adds_user_sibling_preserving_every_original_row(self):
        cid = self.create()
        for text in ("First", "Second", "Later"):
            self.send(cid, text)
        before = self.messages(cid)
        event = self.events(self.send(cid, "Edited", "edit", before[2]["id"]))[0]
        self.assertEqual(event["conversation_id"], cid)
        self.assertEqual(self.tree(cid)["messages"][:len(before)], before)
        rows = self.messages(cid)
        self.assertEqual([r["content"] for r in rows], ["First", "**Hello** world", "Edited", "**Hello** world"])
        self.assertEqual(rows[2]["parent_id"], before[2]["parent_id"])
        self.assertNotIn("Later", str(self.calls[-1]))
        self.assertEqual(len(self.app.load_conversations()), 1)
        self.select(cid, before[2]["id"])
        self.assertEqual(self.messages(cid), before)

    def test_regeneration_and_each_siblings_descendants_are_independent(self):
        cid = self.create()
        self.send(cid, "First")
        original = self.messages(cid)
        self.send(cid, "Only on original")
        branch_one = self.messages(cid)
        event = self.events(self.send(cid, action="regenerate", message_id=original[1]["id"]))[0]
        self.assertEqual(event["user_message_id"], original[0]["id"])
        self.assertEqual(event["conversation_id"], cid)
        self.assertEqual(len(self.calls[-1]["messages"]), 2)
        self.send(cid, "Only on second")
        branch_two = self.messages(cid)
        self.assertNotIn("Only on original", str(self.calls[-1]))
        self.select(cid, original[1]["id"])
        self.assertEqual(self.messages(cid), branch_one)
        self.send(cid, "Continue original")
        self.assertIn("Only on original", str(self.calls[-1]))
        self.assertNotIn("Only on second", str(self.calls[-1]))
        self.select(cid, event["assistant_message_id"])
        self.assertEqual(self.messages(cid), branch_two)
        self.app.init_db()
        self.assertEqual(self.messages(cid), branch_two)
        self.assertEqual(len(self.tree(cid)["messages"]), 9)

    def test_failure_retry_creates_assistant_sibling_and_keeps_failed_content(self):
        cid = self.create()
        self.mode = "partial_error"
        original_request = str(uuid4())
        events = self.events(self.send(cid, generation_id=original_request))
        rows = self.messages(cid)
        self.assertEqual(rows[1]["status"], "error")
        self.assertEqual(rows[1]["content"], "**Hello** ")
        self.assertEqual(events[-1]["status"], "error")
        self.mode = "normal"
        self.send(cid, action="retry", message_id=rows[1]["id"])
        after = self.messages(cid)
        self.assertEqual(after[0]["id"], rows[0]["id"])
        self.assertNotEqual(after[1]["id"], rows[1]["id"])
        self.assertEqual(after[1]["parent_id"], rows[0]["id"])
        self.assertEqual(after[1]["status"], "completed")
        self.assertEqual(self.tree(cid)["messages"][:2], rows)
        self.assertEqual(len(self.calls[-1]["messages"]), 2)
        self.assertEqual(self.send(cid, generation_id=original_request).status_code, 409)
        self.assertEqual(len(self.tree(cid)["messages"]), 3)

    def test_unchanged_edit_and_cross_conversation_parent_are_rejected(self):
        cid, other = self.create(), self.create()
        self.send(cid, "Unchanged")
        before = self.tree(cid)
        self.assertEqual(self.send(cid, "Unchanged", "edit", before["messages"][0]["id"]).status_code, 409)
        response = self.client.post("/chat", json={"conversation_id": other, "message": "Bad parent",
            "generation_id": str(uuid4()), "parent_id": before["messages"][1]["id"]})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.tree(cid), before)
        self.assertEqual(self.client.put(f"/conversations/{other}/branch", json={"message_id": before["messages"][0]["id"]}).status_code, 404)

    def test_cancel_before_post_prevents_late_generation(self):
        cid, gid = self.create(), str(uuid4())
        self.assertEqual(self.client.post(f"/generations/{gid}/stop").json()["status"], "stopped")
        self.assertEqual(self.send(cid, generation_id=gid).status_code, 409)
        self.assertEqual(self.tree(cid)["messages"], [])
        self.assertEqual(self.calls, [])

    def test_parent_edges_cannot_cross_chats_or_be_rewritten(self):
        cid, other = self.create(), self.create()
        self.send(cid)
        first, second = self.messages(cid)
        with closing(self.app.get_connection()) as connection, connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO messages(conversation_id, parent_id, role, content) VALUES (?, ?, 'user', 'invalid')",
                                   (other, second['id']))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute('UPDATE messages SET parent_id = ? WHERE id = ?', (second['id'], first['id']))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute('UPDATE messages SET conversation_id = ? WHERE id = ?', (other, first['id']))

    def test_stopped_turn_can_branch_or_continue_without_partial_model_context(self):
        cid = self.create()
        self.mode = 'slow'
        async def scenario():
            gid = uuid4()
            response = await self.app.chat(self.app.ChatRequest(conversation_id=cid, generation_id=gid, message='Original'))
            iterator = response.body_iterator
            await anext(iterator)
            await anext(iterator)
            await self.app.stop_generation(gid)
            await iterator.aclose()
        asyncio.run(scenario())
        old = self.messages(cid)
        self.mode = 'normal'
        self.send(cid, 'Continue partial')
        self.assertNotIn('**Hello** ', [m['content'] for m in self.calls[-1]['messages']])
        self.send(cid, 'Edited after stop', 'edit', old[0]['id'])
        self.assertEqual(self.tree(cid)['messages'][:2], old)
        self.assertEqual([m['content'] for m in self.calls[-1]['messages'][1:]], ['Edited after stop'])

    def test_search_finds_hidden_branch_and_selects_its_ancestry(self):
        cid = self.create()
        self.send(cid, "hidden pineapple")
        original = self.messages(cid)
        self.send(cid, "different", "edit", original[0]["id"])
        hit = self.client.get("/search?q=pineapple").json()["results"][0]
        self.assertEqual(hit["message_id"], original[0]["id"])
        self.select(cid, hit["message_id"])
        self.assertEqual(self.messages(cid), original)

    def test_upfront_failure_is_visible_and_retriable(self):
        cid = self.create()
        self.mode = "upfront_error"
        self.events(self.send(cid))
        rows = self.messages(cid)
        self.assertEqual(rows[-1]["content"], "")
        self.assertEqual(rows[-1]["status"], "error")
        self.assertFalse(self.app.conversation_lock.locked())

    def stop_case(self, mode):
        cid = self.create()
        self.mode = mode
        async def scenario():
            gid = uuid4()
            response = await self.app.chat(self.app.ChatRequest(conversation_id=cid, generation_id=gid, message="Stop me"))
            iterator = response.body_iterator
            self.assertIn('"start"', await anext(iterator))
            if mode == "slow":
                self.assertIn('"delta"', await anext(iterator))
            result = await self.app.stop_generation(gid)
            self.assertEqual(result["status"], "stopped")
            await iterator.aclose()
            self.assertEqual((await self.app.stop_generation(gid))["status"], "stopped")
        asyncio.run(scenario())
        rows = self.messages(cid)
        self.assertEqual(rows[-1]["status"], "stopped")
        self.assertEqual(rows[-1]["content"], "**Hello** " if mode == "slow" else "")
        self.assertTrue(self.clients[-1].closed)
        self.assertFalse(self.app.conversation_lock.locked())
        self.assertEqual(self.app.load_messages(cid), [{"role": "user", "content": "Stop me"}])

    def test_stop_before_first_token(self):
        self.stop_case("waiting")

    def test_stop_scheduled_before_producer_starts_releases_reservation(self):
        cid = self.create()
        self.mode = 'waiting'
        async def scenario():
            gid = uuid4()
            # Queue Stop first, then reserve the generation without yielding.
            # Stop sees the job before its producer has had a scheduler turn.
            stopping = asyncio.create_task(self.app.stop_generation(gid))
            response = await self.app.chat(self.app.ChatRequest(
                conversation_id=cid, generation_id=gid, message='Immediate stop'))
            self.assertEqual((await stopping)['status'], 'stopped')
            await response.body_iterator.aclose()
        asyncio.run(asyncio.wait_for(scenario(), timeout=5))
        self.assertFalse(self.app.conversation_lock.locked())
        self.assertEqual(self.messages(cid)[-1]['status'], 'stopped')

    def test_stop_after_partial_token(self):
        self.stop_case("slow")

    def test_stop_during_completion_waits_for_cleanup_without_recancelling(self):
        cid = self.create()
        async def scenario():
            closing_started, release_close = asyncio.Event(), asyncio.Event()
            model = Model(self)
            async def slow_close():
                closing_started.set()
                await release_close.wait()
                model.closed = True
            model.close = slow_close
            with patch.object(self.app.ollama, "AsyncClient", return_value=model):
                gid = uuid4()
                await self.app.chat(self.app.ChatRequest(conversation_id=cid, generation_id=gid, message="Finish"))
                await closing_started.wait()
                stopping = asyncio.create_task(self.app.stop_generation(gid))
                await asyncio.sleep(0)
                release_close.set()
                self.assertEqual((await stopping)["status"], "completed")
                self.assertTrue(model.closed)
        asyncio.run(scenario())
        self.assertFalse(self.app.conversation_lock.locked())

    def test_disconnect_cancels_model_and_releases_lock(self):
        cid = self.create()
        self.mode = "slow"
        async def scenario():
            response = await self.app.chat(self.app.ChatRequest(conversation_id=cid, generation_id=uuid4(), message="Disconnect"))
            async def receive():
                return {"type": "http.disconnect"}
            async def send(event):
                if event["type"] == "http.response.body":
                    raise OSError("Disconnected")
            with self.assertRaises(Exception):
                await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
        asyncio.run(scenario())
        self.assertFalse(self.app.conversation_lock.locked())
        self.assertEqual(self.messages(cid)[-1]["status"], "stopped")

    def test_conflicting_actions_and_deletion_rejected_during_generation(self):
        cid = self.create()
        self.mode = "waiting"
        async def scenario():
            gid = uuid4()
            await self.app.chat(self.app.ChatRequest(conversation_id=cid, generation_id=gid, message="Wait"))
            with self.assertRaises(self.app.HTTPException) as error:
                await self.app.chat(self.app.ChatRequest(conversation_id=cid, generation_id=uuid4(), message="Conflict"))
            self.assertEqual(error.exception.status_code, 409)
            with self.assertRaises(self.app.HTTPException):
                self.app.delete_conversation(cid)
            await self.app.stop_generation(gid)
        asyncio.run(scenario())

    def test_target_validation_and_duplicate_request_rejected(self):
        a, b = self.create(), self.create()
        gid = str(uuid4())
        self.send(a, generation_id=gid)
        rows = self.messages(a)
        self.assertEqual(self.send(a, generation_id=gid).status_code, 409)
        self.assertEqual(self.send(b, "Bad", "edit", rows[0]["id"]).status_code, 404)
        self.assertEqual(self.send(a, "Bad", "edit", rows[1]["id"]).status_code, 422)
        self.assertEqual(self.send(a, "  ").status_code, 422)
        self.assertEqual(self.send(9999).status_code, 404)
        self.assertEqual(self.messages(a), rows)
        self.assertEqual(self.messages(b), [])

    def test_delete_conversation_cascades_all_branches_and_choices(self):
        cid = self.create()
        self.send(cid)
        row = self.messages(cid)[0]
        self.send(cid, "Edit", "edit", row["id"])
        self.assertEqual(self.client.delete(f"/conversations/{cid}").status_code, 200)
        with closing(self.app.get_connection()) as connection:
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            for table in ("messages", "branch_selections", "generation_requests"):
                self.assertEqual(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)

    def test_feedback_is_scoped_persistent_and_toggleable(self):
        cid, other = self.create(), self.create()
        self.send(cid)
        row = self.messages(cid)[1]
        url = f"/conversations/{cid}/messages/{row['id']}/feedback"
        for value in (1, -1, None):
            self.assertEqual(self.client.patch(url, json={"value": value}).status_code, 200)
            self.assertEqual(self.messages(cid)[1]["feedback"], value)
        self.assertEqual(self.client.patch(url, json={"value": 2}).status_code, 422)
        self.assertEqual(self.client.patch(f"/conversations/{other}/messages/{row['id']}/feedback", json={"value": 1}).status_code, 404)

    def test_search_rename_unicode_and_safe_literal_matching(self):
        cid = self.create()
        self.send(cid, "ß" * 300 + " 😀 Straße <img> 100%")
        self.client.patch(f"/conversations/{cid}", json={"title": "My notes"})
        hit = self.client.get("/search", params={"q": "STRASSE"}).json()["results"][0]
        self.assertEqual(hit["conversation_id"], cid)
        self.assertEqual([hit["snippet"][a:b] for a, b in hit["snippet_matches"]], ["Straße"])
        self.assertEqual(self.client.get("/search", params={"q": "' OR 1=1 --"}).json()["results"], [])
        self.assertEqual(self.client.get("/search", params={"q": " "}).json()["results"], [])
        self.assertEqual(self.app.load_conversations()[0]["title"], "My notes")

    def test_restart_preserves_history_and_marks_interrupted_response(self):
        cid = self.create()
        self.send(cid)
        with closing(self.app.get_connection()) as connection, connection:
            connection.execute("UPDATE messages SET status = 'generating' WHERE role = 'assistant'")
        self.app.init_db()
        self.assertEqual(self.messages(cid)[1]["status"], "stopped")
        self.assertEqual(self.messages(cid)[1]["content"], "**Hello** world")

    def test_stage6_upgrade_preserves_messages_and_backfills_relationships(self):
        path = self.root / "old.db"
        with closing(sqlite3.connect(path)) as c, c:
            c.executescript("""CREATE TABLE conversations(id INTEGER PRIMARY KEY, title TEXT, title_source TEXT, created_at TEXT, updated_at TEXT);
                INSERT INTO conversations VALUES (1, 'Old', 'manual', '2026-01-01', '2026-01-01');
                CREATE TABLE messages(id INTEGER PRIMARY KEY, conversation_id INTEGER REFERENCES conversations(id) ON DELETE CASCADE, role TEXT, content TEXT, created_at TEXT);
                INSERT INTO messages VALUES (1,1,'user','Keep me','2026-01-01');
                INSERT INTO messages VALUES (2,1,'assistant','Still here','2026-01-01');""")
        self.app.DATABASE = path
        self.app.init_db()
        rows = self.messages(1)
        self.assertEqual(rows[1]["reply_to_message_id"], 1)
        self.assertEqual(rows[1]["parent_id"], 1)
        self.assertIsNone(rows[0]["parent_id"])
        self.app.init_db()
        self.assertEqual(self.messages(1), rows)
        self.assertEqual(rows[1]["content"], "Still here")
        self.assertEqual(rows[1]["status"], "completed")

    def test_assets_are_available(self):
        for url in ("/", "/static/script.js", "/static/composer.js", "/static/style.css"):
            self.assertEqual(self.client.get(url).status_code, 200)

    def test_auto_title_still_uses_first_complete_exchange(self):
        cid = self.create()
        title_client = Mock()
        title_client.chat.return_value = {"message": {"content": "Learning Python"}}
        with patch.object(self.app.ollama, "Client", return_value=title_client):
            self.send(cid, "Teach me Python")
            self.send(cid, "Next steps")
        self.assertEqual(self.app.load_conversations()[0]["title"], "Learning Python")
        self.assertEqual(title_client.chat.call_count, 1)
        self.assertIn("Teach me Python", title_client.chat.call_args.kwargs["messages"][1]["content"])

    def test_failed_final_save_never_reports_success(self):
        cid = self.create()
        real_save = self.app.save_generation
        def fail_completed(message_id, content, status):
            if status == "completed":
                raise sqlite3.OperationalError("Disk full")
            return real_save(message_id, content, status)
        with patch.object(self.app, "save_generation", side_effect=fail_completed):
            events = self.events(self.send(cid))
        self.assertEqual(events[-1]["status"], "error")
        self.assertTrue(all(e["generation_id"] == events[0]["generation_id"] for e in events))
        self.assertTrue(any(e["type"] == "error" and "saved" in e["message"] for e in events))
        self.assertFalse(self.app.conversation_lock.locked())


if __name__ == "__main__":
    unittest.main(verbosity=2)
