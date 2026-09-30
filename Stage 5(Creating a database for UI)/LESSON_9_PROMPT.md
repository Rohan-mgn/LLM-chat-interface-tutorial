# Lesson 9 — SQLite persistence implementation prompt

This records the Lesson 9 implementation requirements with the database path
and model confirmed during development. Stage 5 already implements this brief.

Inspect the existing Stage 5 project before editing. Modify the existing
implementation without an unnecessary rewrite. Preserve its UI, system prompt,
conversation context, Ollama streaming, Markdown rendering, and HTML sanitization.

## Database

Use Python's built-in `sqlite3`; do not add an ORM. Derive the database path
from `main.py` with `pathlib.Path`:

```python
BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
DATA_DIR = PROJECT_DIR / "data"
DATABASE = DATA_DIR / "chat.db"
```

For this workspace the confirmed path is:

```text
D:\Projects\LLM chat wrapper tutorial\data\chat.db
```

This replaces the earlier proposed `D:\Projects\LLM chat wrapper\data\chat.db`.
Do not relocate the database to that other project, C:, a browser profile,
a temporary directory, or the terminal's current directory. Automatically
create `data/` and print the resolved database path once per server startup.

Create the table if it does not exist:

```sql
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

Implement initialization, `save_message()`, `load_messages()`, and
`clear_messages()`. Load messages in increasing ID order. Use `?` parameters
for message values, commit successful writes, and close every connection.
Never interpolate user-controlled content into SQL.

## API and browser flow

1. Make FastAPI and SQLite the source of truth. The browser sends only
   `{ "message": "..." }` to `POST /chat`; use a request model with `message: str`.
2. Save the new user message, load full SQLite history, and prepend the existing
   system prompt. Send it to the confirmed Ollama model, `llama3.2:3b`.
3. Keep `stream=True` and FastAPI `StreamingResponse`. Yield chunks immediately
   while accumulating the answer. Save one complete `assistant` row after
   successful generation; never save individual chunks or failed partial replies.
4. Add `GET /messages`, returning `{ "messages": [{ "role": "...", "content": "..." }] }`.
   Call it on page startup and rebuild the conversation in order.
5. Render restored assistant Markdown with Marked and DOMPurify. Use literal
   text APIs for user messages.
6. Add `DELETE /messages`. New chat deletes all rows in the single conversation.
   Clear the visible chat and focus the textarea after backend confirmation.
7. Remove `localStorage` chat saves and restores. Keep JSON serialization for
   HTTP requests; it is still needed to send the newest message.
8. Preserve Enter-to-send, Shift+Enter newlines, scrolling, disabled controls
   during generation, error handling, user bubbles, and responsive layout.
9. Keep API routes before static mounts. Do not expose the database as a static file.
10. Keep this lesson limited to one conversation. Multiple saved conversations
    and a sidebar that switches between them belong to Lesson 10.

## Verification and explanation

Verify the resolved database path, schema, parameterized SQL, and connection
cleanup. Use isolated test databases under this project's D: drive directory
so tests cannot clear a real conversation.

Test preference recall, refresh restoration, context after refresh, persistence
after a Uvicorn restart, New chat deletion followed by a fresh conversation,
progressive streaming, and one database row per completed assistant reply.
Check headings, bold text, lists, tables, and fenced code blocks before and
after refresh. Review frontend/backend request agreement and failure recovery.

Document why backend SQLite persistence differs from browser `localStorage`,
why parameterized SQL is necessary, and why New chat deletes the one saved
conversation in Lesson 9.
