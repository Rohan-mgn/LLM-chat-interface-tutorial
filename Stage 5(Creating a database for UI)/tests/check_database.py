r"""Check Lesson 10 using isolated SQLite files and deterministic Ollama streams.

Run from the project root:
    .\.venv\Scripts\python.exe -B "Stage 5(Creating a database for UI)/tests/check_database.py"

Each test uses its own database beneath this project's data directory. The
application's real conversation database is never opened or cleared by these tests.
"""

import asyncio
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import shutil
import sqlite3
import unittest
from unittest.mock import patch
import uuid

from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect


STAGE = Path(__file__).resolve().parents[1]
PROJECT = STAGE.parent


def import_app():
    spec = importlib.util.spec_from_file_location(
        "lesson10_" + uuid.uuid4().hex, STAGE / "main.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def chunks(*parts):
    return iter({"message": {"content": text}} for text in parts)


class DatabaseChecks(unittest.TestCase):
    def setUp(self):
        self.test_root = PROJECT / "data" / ("lesson10-check-" + uuid.uuid4().hex)
        self.test_root.mkdir(parents=True)
        self.module = import_app()
        self.database = self.test_root / "nested" / "chat.db"
        self.module.DATA_DIR = self.database.parent
        self.module.DATABASE = self.database
        self.startup_output = io.StringIO()
        self.client_context = TestClient(self.module.app)
        with contextlib.redirect_stdout(self.startup_output):
            self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        # Only remove the unique directory this test created inside the project.
        resolved = self.test_root.resolve()
        self.assertEqual(resolved.parent, (PROJECT / "data").resolve())
        self.assertTrue(resolved.name.startswith("lesson10-check-"))
        shutil.rmtree(resolved)

    def create(self):
        response = self.client.post("/conversations")
        self.assertEqual(response.status_code, 201)
        conversation = response.json()
        self.assertGreater(conversation["id"], 0)
        self.assertEqual(conversation["title"], "New chat")
        self.assertTrue(conversation["created_at"])
        self.assertTrue(conversation["updated_at"])
        return conversation["id"]

    def stored(self, conversation_id):
        response = self.client.get(f"/conversations/{conversation_id}/messages")
        self.assertEqual(response.status_code, 200)
        return response.json()["messages"]

    def send(self, conversation_id, message):
        return self.client.post(
            "/chat", json={"conversation_id": conversation_id, "message": message}
        )

    def assert_new_turn_works(self, conversation_id):
        with patch.object(self.module.ollama, "chat", return_value=chunks("Recovered.")):
            response = self.send(conversation_id, "Try again")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "Recovered.")

    def legacy_database(self, rows):
        """Build a real Lesson 9 schema in a separate, disposable database."""
        module = import_app()
        module.DATA_DIR = self.test_root / "legacy"
        module.DATABASE = module.DATA_DIR / "chat.db"
        module.DATA_DIR.mkdir()
        with contextlib.closing(sqlite3.connect(module.DATABASE)) as connection, connection:
            connection.execute("""
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            connection.executemany(
                "INSERT INTO messages (id, role, content, created_at) VALUES (?, ?, ?, ?)",
                rows,
            )
        return module

    def test_absolute_database_path_does_not_follow_terminal_directory(self):
        original = import_app().DATABASE
        previous_cwd = Path.cwd()
        try:
            os.chdir(self.test_root)
            changed = import_app().DATABASE
        finally:
            os.chdir(previous_cwd)
        self.assertTrue(original.is_absolute())
        self.assertEqual(original, changed)
        self.assertEqual(original, PROJECT / "data" / "chat.db")
        self.assertEqual(original.drive.upper(), "D:")

    def test_startup_creates_schema_and_prints_path_once(self):
        self.assertTrue(self.database.is_file())
        self.assertEqual(self.startup_output.getvalue().count(str(self.database)), 1)
        with contextlib.closing(self.module.get_connection()) as connection:
            columns = {
                row["name"]: row for row in connection.execute("PRAGMA table_info(messages)")
            }
            conversation_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(conversations)")
            }
            schema = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                ("messages",),
            ).fetchone()["sql"]
            indexes = list(connection.execute("PRAGMA index_list(messages)"))
            indexed_columns = []
            for index in indexes:
                # Index names come from SQLite, not a user-controlled request.
                indexed_columns.extend(
                    row["name"] for row in connection.execute(
                        "SELECT name FROM pragma_index_info(?)", (index["name"],)
                    )
                )
        self.assertTrue({"id", "conversation_id", "role", "content", "created_at"}.issubset(columns))
        self.assertTrue({"id", "title", "created_at", "updated_at"}.issubset(conversation_columns))
        self.assertEqual(columns["id"]["pk"], 1)
        for field in ("conversation_id", "role", "content"):
            self.assertEqual(columns[field]["notnull"], 1)
        self.assertIn("AUTOINCREMENT", schema.upper())
        self.assertIn("CURRENT_TIMESTAMP", schema.upper())
        self.assertIn("conversation_id", indexed_columns)
        self.assertEqual(self.client.get("/conversations").json(), {"conversations": []})

    def test_connection_uses_rows_and_enforces_foreign_keys_on_every_connection(self):
        for _ in range(2):
            with contextlib.closing(self.module.get_connection()) as connection:
                self.assertIs(connection.row_factory, sqlite3.Row)
                self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                foreign_key = connection.execute("PRAGMA foreign_key_list(messages)").fetchone()
                self.assertEqual(foreign_key["table"], "conversations")
                self.assertEqual(foreign_key["from"], "conversation_id")
                self.assertEqual(foreign_key["to"], "id")
                self.assertEqual(foreign_key["on_delete"], "CASCADE")
        with self.assertRaises(sqlite3.IntegrityError):
            self.module.save_message(999999, "user", "No parent conversation")

    def test_deleting_one_conversation_cascades_only_its_messages(self):
        first, second = self.create(), self.create()
        self.module.save_message(first, "user", "First chat")
        self.module.save_message(first, "assistant", "First reply")
        self.module.save_message(second, "user", "Second chat")
        with contextlib.closing(self.module.get_connection()) as connection, connection:
            connection.execute("DELETE FROM conversations WHERE id = ?", (first,))
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (first,)
            ).fetchone()[0], 0)
        self.assertFalse(self.module.conversation_exists(first))
        self.assertTrue(self.module.conversation_exists(second))
        self.assertEqual(self.stored(second), [{"role": "user", "content": "Second chat"}])

    def test_helpers_store_literal_text_in_order_and_preserve_existing_rows(self):
        conversation_id = self.create()
        text = "Robert'); DROP TABLE messages; --\nPython \U0001f40d"
        expected = [
            {"role": "user", "content": text},
            {"role": "assistant", "content": "## Answer\n\n**Python**"},
            {"role": "user", "content": "Another message"},
        ]
        for message in expected:
            self.module.save_message(conversation_id, **message)
        self.module.init_db()
        self.assertEqual(self.module.load_messages(conversation_id), expected)
        self.assertEqual(self.stored(conversation_id), expected)
        with contextlib.closing(self.module.get_connection()) as connection:
            rows = connection.execute("SELECT id, created_at FROM messages ORDER BY id").fetchall()
        self.assertEqual([row["id"] for row in rows], sorted(row["id"] for row in rows))
        self.assertTrue(all(row["created_at"] for row in rows))

    def test_migration_preserves_legacy_ids_roles_content_and_timestamps(self):
        rows = [
            (4, "user", "Keep my pineapple secret. \U0001f34d", "2026-01-01 11:22:33"),
            (9, "assistant", "## Saved\n\n**Pineapple**", "2026-01-01 11:22:34"),
        ]
        module = self.legacy_database(rows)
        module.init_db()
        conversations = module.load_conversations()
        self.assertEqual(len(conversations), 1)
        imported = conversations[0]
        self.assertEqual(imported["title"], "Imported chat")
        with contextlib.closing(module.get_connection()) as connection:
            restored = connection.execute(
                "SELECT id, role, content, created_at FROM messages ORDER BY id"
            ).fetchall()
            parents = connection.execute("SELECT DISTINCT conversation_id FROM messages").fetchall()
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual([tuple(row) for row in restored], rows)
        self.assertEqual([row[0] for row in parents], [imported["id"]])
        module.save_message(imported["id"], "user", "And after migration?")
        with contextlib.closing(module.get_connection()) as connection:
            self.assertGreater(connection.execute("SELECT MAX(id) FROM messages").fetchone()[0], 9)
        module.init_db()
        self.assertEqual(len(module.load_conversations()), 1)
        self.assertEqual(len(module.load_messages(imported["id"])), 3)

    def test_migrating_empty_legacy_database_creates_no_imported_chat(self):
        module = self.legacy_database([])
        module.init_db()
        module.init_db()
        self.assertEqual(module.load_conversations(), [])
        conversation = module.create_conversation()
        module.save_message(conversation["id"], "user", "Fresh start")
        self.assertEqual(module.load_messages(conversation["id"]), [
            {"role": "user", "content": "Fresh start"}
        ])

    def test_migration_failure_rolls_back_schema_and_preserves_legacy_history(self):
        rows = [(7, "user", "Do not lose this", "2026-01-02 03:04:05")]
        module = self.legacy_database(rows)
        original_get_connection = module.get_connection

        def failing_connection():
            connection = original_get_connection()
            connection.set_authorizer(
                lambda action, *_: sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_DROP_TABLE else sqlite3.SQLITE_OK
            )
            return connection

        with patch.object(module, "get_connection", side_effect=failing_connection):
            with self.assertRaises(sqlite3.DatabaseError):
                module.init_db()
        with contextlib.closing(sqlite3.connect(module.DATABASE)) as connection:
            columns = [row[1] for row in connection.execute("PRAGMA table_info(messages)")]
            self.assertEqual(columns, ["id", "role", "content", "created_at"])
            self.assertEqual(connection.execute("SELECT * FROM messages").fetchall(), rows)
            names = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )}
            self.assertEqual(names, {"messages", "sqlite_sequence"})
        module.init_db()
        self.assertEqual(module.load_conversations()[0]["title"], "Imported chat")

    def test_api_rejects_invalid_ids_blank_messages_and_browser_owned_history(self):
        conversation_id = self.create()
        payloads = [
            {},
            {"message": "Hello"},
            {"conversation_id": conversation_id, "message": " \n\t "},
            {"conversation_id": conversation_id, "message": None},
            {"conversation_id": conversation_id, "messages": [{"role": "user", "content": "Old API"}]},
            {"conversation_id": conversation_id, "message": "Hello", "messages": []},
        ]
        payloads.extend({"conversation_id": value, "message": "Hello"}
                        for value in (None, 0, -1, "1", 1.5, True))
        with patch.object(self.module.ollama, "chat") as model:
            for payload in payloads:
                with self.subTest(payload=payload):
                    self.assertEqual(self.client.post("/chat", json=payload).status_code, 422)
                    self.assertEqual(self.stored(conversation_id), [])
            model.assert_not_called()

    def test_unknown_conversation_returns_404_without_saving_or_generating(self):
        with patch.object(self.module.ollama, "chat") as model:
            self.assertEqual(self.send(999999, "Hello").status_code, 404)
            self.assertEqual(self.client.get("/conversations/999999/messages").status_code, 404)
            model.assert_not_called()
        self.assertEqual(self.module.load_conversations(), [])
        self.assertFalse(self.module.conversation_lock.locked())

    def test_database_history_model_and_system_prompt_reach_ollama(self):
        conversation_id = self.create()
        first = "My favorite programming language is Python."
        follow_up = "What programming language did I say I like?"
        replies = ["I'll remember that.", "You said you like Python."]
        with patch.object(
            self.module.ollama,
            "chat",
            side_effect=[chunks("I'll ", "remember that."), chunks("You said ", "you like Python.")],
        ) as model:
            response = self.send(conversation_id, "  " + first + "  ")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.text, replies[0])
            self.assertTrue(response.headers["content-type"].startswith("text/plain"))
            response = self.send(conversation_id, follow_up)
            self.assertEqual(response.text, replies[1])
        expected = [
            {"role": "user", "content": first},
            {"role": "assistant", "content": replies[0]},
            {"role": "user", "content": follow_up},
            {"role": "assistant", "content": replies[1]},
        ]
        self.assertEqual(self.stored(conversation_id), expected)
        for index, call in enumerate(model.call_args_list):
            arguments = call.kwargs
            self.assertEqual(arguments["model"], self.module.MODEL)
            self.assertIs(arguments["stream"], True)
            self.assertEqual(arguments["messages"][0], {
                "role": "system", "content": self.module.SYSTEM_PROMPT
            })
            self.assertIn("information about yourself", self.module.SYSTEM_PROMPT)
            self.assertIn("Use Markdown", self.module.SYSTEM_PROMPT)
            self.assertEqual(arguments["messages"][1:], expected[:1 if index == 0 else 3])

    def test_pineapple_secret_stays_in_conversation_a_when_switching_to_b_and_back(self):
        first, second = self.create(), self.create()

        def contextual_model(**arguments):
            user_history = [message["content"] for message in arguments["messages"]
                            if message["role"] == "user"]
            if any("pineapple" in text for text in user_history):
                return chunks("Your secret word is ", "pineapple.")
            return chunks("You have not told me a secret word in this conversation.")

        with patch.object(self.module.ollama, "chat", side_effect=contextual_model) as model:
            self.assertEqual(self.send(first, "My secret test word is pineapple.").status_code, 200)
            self.assertIn("pineapple", self.send(first, "What is my secret test word?").text)
            before = self.stored(first)
            second_reply = self.send(second, "What is my secret test word?")
            self.assertNotIn("pineapple", second_reply.text)
            self.assertEqual(model.call_args.kwargs["messages"][1:], [
                {"role": "user", "content": "What is my secret test word?"}
            ])
            self.assertEqual(self.stored(first), before)
            self.assertIn("pineapple", self.send(first, "What is my secret word again?").text)
            self.assertEqual(model.call_args.kwargs["messages"][1:], before + [
                {"role": "user", "content": "What is my secret word again?"}
            ])
        self.assertEqual(len(self.stored(second)), 2)

    def test_initial_title_collapses_whitespace_and_does_not_change_on_followups(self):
        conversation_id = self.create()
        with patch.object(self.module.ollama, "chat", side_effect=lambda **_: chunks("Saved.")):
            self.assertEqual(self.send(conversation_id, "  First\n\t  chat   title  ").status_code, 200)
            first_title = self.module.load_conversations()[0]["title"]
            self.assertEqual(first_title, "First chat title")
            self.assertEqual(self.send(conversation_id, "A completely different follow-up").status_code, 200)
            self.assertEqual(self.module.load_conversations()[0]["title"], first_title)

    def test_initial_title_has_length_limit_even_when_first_message_says_new_chat(self):
        first, second = self.create(), self.create()
        with patch.object(self.module.ollama, "chat", side_effect=lambda **_: chunks("Saved.")):
            self.send(first, "x" * 100)
            self.send(second, "New chat")
            self.send(second, "This should not replace the first title")
        titles = {item["id"]: item["title"] for item in self.module.load_conversations()}
        self.assertTrue(0 < len(titles[first]) <= 60)
        self.assertEqual(titles[second], "New chat")

    def test_conversation_list_is_ordered_by_recent_activity(self):
        first, second = self.create(), self.create()
        with contextlib.closing(self.module.get_connection()) as connection, connection:
            connection.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", ("2000-01-01 00:00:00", first))
            connection.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", ("2001-01-01 00:00:00", second))
        self.assertEqual([item["id"] for item in self.module.load_conversations()], [second, first])
        self.module.save_message(first, "user", "Reopen the older chat")
        response = self.client.get("/conversations")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.json()["conversations"]], [first, second])

    def test_streaming_yields_chunks_before_saving_one_complete_assistant_row(self):
        conversation_id = self.create()
        produced = []

        def model_stream(**kwargs):
            for content in ("Hello ", "", "Python!"):
                produced.append(content)
                yield {"message": {"content": content}}

        async def inspect(response):
            iterator = response.body_iterator
            self.assertEqual(await anext(iterator), "Hello ")
            self.assertEqual(produced, ["Hello "])
            self.assertEqual(self.module.load_messages(conversation_id), [{"role": "user", "content": "Hi"}])
            self.assertEqual(await anext(iterator), "Python!")
            self.assertEqual(len(self.module.load_messages(conversation_id)), 1)
            with self.assertRaises(StopAsyncIteration):
                await anext(iterator)

        with patch.object(self.module.ollama, "chat", side_effect=model_stream):
            response = self.module.chat(self.module.ChatRequest(conversation_id=conversation_id, message="Hi"))
            try:
                asyncio.run(inspect(response))
            finally:
                response.close()
        self.assertEqual(self.stored(conversation_id), [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello Python!"},
        ])

    def test_active_generation_rejects_other_turns_but_does_not_delete_old_chats(self):
        first, second = self.create(), self.create()
        with patch.object(self.module.ollama, "chat", return_value=chunks("Hello", " world")):
            response = self.module.chat(self.module.ChatRequest(conversation_id=first, message="Hi"))
            try:
                self.assertEqual(self.send(first, "Concurrent same chat").status_code, 409)
                self.assertEqual(self.send(second, "Concurrent other chat").status_code, 409)
                self.assertEqual(self.stored(first), [{"role": "user", "content": "Hi"}])
                self.assertEqual(self.stored(second), [])
                self.create()  # Creating an empty row never mutates the active stream.
                self.assertEqual(self.client.delete("/messages").status_code, 404)
            finally:
                response.close()
        self.assert_new_turn_works(second)

    def test_upfront_model_failure_preserves_user_without_assistant_and_releases_lock(self):
        conversation_id = self.create()
        with patch.object(self.module.ollama, "chat", side_effect=RuntimeError("Ollama unavailable")):
            response = self.send(conversation_id, "Hello")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.stored(conversation_id), [{"role": "user", "content": "Hello"}])
        self.assert_new_turn_works(conversation_id)

    def test_empty_response_preserves_user_without_assistant_and_releases_lock(self):
        conversation_id = self.create()
        with patch.object(self.module.ollama, "chat", return_value=chunks("", "")):
            response = self.send(conversation_id, "Hello")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.stored(conversation_id), [{"role": "user", "content": "Hello"}])
        self.assert_new_turn_works(conversation_id)

    def test_midstream_failure_does_not_store_partial_assistant(self):
        conversation_id = self.create()
        closed = []

        def failing_stream(**kwargs):
            try:
                yield {"message": {"content": "Only part of the answer"}}
                raise RuntimeError("Model connection interrupted")
            finally:
                closed.append(True)

        with patch.object(self.module.ollama, "chat", side_effect=failing_stream):
            with self.assertRaisesRegex(RuntimeError, "Model connection interrupted"):
                self.send(conversation_id, "Hello")
        self.assertEqual(closed, [True])
        self.assertFalse(self.module.conversation_lock.locked())
        self.assertEqual(self.stored(conversation_id), [{"role": "user", "content": "Hello"}])
        self.assert_new_turn_works(conversation_id)

    def test_client_disconnect_closes_stream_without_saving_partial_assistant(self):
        conversation_id = self.create()
        closed, sent = [], []

        def model_stream(**kwargs):
            try:
                yield {"message": {"content": "First chunk"}}
                yield {"message": {"content": " must not be saved"}}
            finally:
                closed.append(True)

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)
            if message["type"] == "http.response.body":
                raise OSError("Browser disconnected")

        with patch.object(self.module.ollama, "chat", side_effect=model_stream):
            response = self.module.chat(self.module.ChatRequest(conversation_id=conversation_id, message="Hello"))
            with self.assertRaises(ClientDisconnect):
                asyncio.run(response(
                    {"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send
                ))
        self.assertEqual(sent[0]["type"], "http.response.start")
        self.assertEqual(sent[1]["body"], b"First chunk")
        self.assertTrue(sent[1]["more_body"])
        self.assertEqual(len(sent), 2)
        self.assertEqual(closed, [True])
        self.assertFalse(self.module.conversation_lock.locked())
        self.assertEqual(self.stored(conversation_id), [{"role": "user", "content": "Hello"}])
        self.assert_new_turn_works(conversation_id)

    def test_restarting_app_keeps_both_conversations_and_selected_followup_context(self):
        first, second = self.create(), self.create()
        self.module.save_message(first, "user", "My favorite programming language is Python.")
        self.module.save_message(first, "assistant", "I'll remember Python.")
        self.module.save_message(second, "user", "This separate chat is about gardening.")
        before_first, before_second = self.stored(first), self.stored(second)
        restarted = import_app()
        restarted.DATA_DIR = self.database.parent
        restarted.DATABASE = self.database
        with contextlib.redirect_stdout(io.StringIO()), TestClient(restarted.app) as client:
            self.assertEqual(len(client.get("/conversations").json()["conversations"]), 2)
            self.assertEqual(client.get(f"/conversations/{first}/messages").json(), {"messages": before_first})
            self.assertEqual(client.get(f"/conversations/{second}/messages").json(), {"messages": before_second})
            with patch.object(restarted.ollama, "chat", return_value=chunks("Python!")) as model:
                response = client.post("/chat", json={"conversation_id": first, "message": "Which language was it?"})
            self.assertEqual(response.text, "Python!")
            self.assertEqual(model.call_args.kwargs["messages"][1:], before_first + [
                {"role": "user", "content": "Which language was it?"}
            ])
        self.assertEqual(self.stored(first)[-1], {"role": "assistant", "content": "Python!"})
        self.assertEqual(self.stored(second), before_second)

    def test_new_conversation_keeps_old_messages_and_starts_with_empty_context(self):
        old_id = self.create()
        self.module.save_message(old_id, "user", "Old conversation")
        self.module.save_message(old_id, "assistant", "Old reply")
        before = self.stored(old_id)
        new_id = self.create()
        self.assertNotEqual(new_id, old_id)
        self.assertEqual(self.stored(old_id), before)
        self.assertEqual(self.stored(new_id), [])
        with patch.object(self.module.ollama, "chat", return_value=chunks("Fresh reply")) as model:
            response = self.send(new_id, "Fresh question")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(model.call_args.kwargs["messages"][1:], [{"role": "user", "content": "Fresh question"}])
        self.assertEqual(self.stored(old_id), before)

    def test_page_assets_new_routes_and_removed_destructive_endpoint(self):
        for route, path in (
            ("/", STAGE / "static" / "index.html"),
            ("/static/style.css", STAGE / "static" / "style.css"),
            ("/static/script.js", STAGE / "static" / "script.js"),
        ):
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, path.read_bytes())
        self.assertEqual(self.client.get("/conversations").status_code, 200)
        self.assertEqual(self.client.get("/messages").status_code, 404)
        self.assertEqual(self.client.delete("/messages").status_code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
