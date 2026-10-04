# Stage 6 — Conversation management and chat search

Stage 6 implements Lessons 11 and 12 on top of Lesson 10's isolated conversations,
streamed Markdown replies, and SQLite persistence. It runs independently of the
earlier stages and stores history at `data/stage6/chat.db` in the project root.

## Run

From the project root, in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r "Stage 6(Better conversation management and Chat search)/requirements-dev.txt"
ollama pull llama3.2:3b
.\.venv\Scripts\python.exe "Stage 6(Better conversation management and Chat search)/run.py"
```

Keep Ollama running and visit `http://127.0.0.1:8000`. The launcher reloads when
application files change. Use one server worker: generation and deletion share
an in-process lock to prevent interleaved turns and deletion during a reply.

## Lesson 11 — Complete

- [x] Generate an automatic title from the first user message and first completed assistant reply.
- [x] Immediately use the first message as a fallback; retain it if title generation fails.
- [x] Limit generated and manual titles to 60 characters and truncate sidebar text visually.
- [x] Expose full titles as accessible button text and hover titles.
- [x] Provide a `⋯` conversation menu with Rename and Delete.
- [x] Confirm deletion, allow cancellation, and report failures without losing the current chat.
- [x] Delete all associated messages through SQLite `ON DELETE CASCADE`.
- [x] Enable foreign keys on every connection and validate references at startup.
- [x] Highlight the active conversation and preserve selection when the sidebar refreshes.
- [x] Deduplicate sidebar entries and ignore out-of-order refresh responses.
- [x] Update `updated_at` with each saved user or assistant message and sort newest first.
- [x] Group conversations into Today, Yesterday, Previous 7 Days, Previous 30 Days, and Older.
- [x] Use the browser's local calendar dates for groups; store timestamps in UTC.
- [x] Select the most recently updated remaining chat after deleting the active chat.
- [x] Return to New Chat with an empty sidebar state after deleting the last chat.
- [x] Preserve other conversations, streaming, sanitized Markdown, and persistent history.

Automatic title generation runs after the response stream closes and releases
the generation lock. Its separate Ollama request uses a 12-second HTTP timeout
and a 32-token output budget. The sidebar polls briefly to show the resulting
title. Manual renaming wins over a title request already in progress; deletion
cannot resurrect a chat. Title changes do not change message activity dates.
Existing Stage 5 schemas receive a `title_source` column without overwriting
their titles. An interrupted title job retains its fallback after restart.

## Lesson 12 — Complete

- [x] Search titles and message contents across conversations through `GET /search?q=...`.
- [x] Use parameterized SQLite queries and literal matching, including `%`, `_`, and quotes.
- [x] Match case-insensitively with Unicode casefolding, including `STRASSE` → `Straße`.
- [x] Return matching conversation IDs, titles, message IDs, roles, and bounded contextual snippets.
- [x] Show one result per conversation with its first matching message, or a title-only result.
- [x] Highlight matches using text and `mark` nodes; never interpret search content as HTML.
- [x] Keep highlight offsets correct when Unicode casefolding changes text length or emoji appear.
- [x] Open the correct conversation and scroll to, outline, and focus its matching message.
- [x] Show search results separately from the normal sidebar with clear loading, empty, and error states.
- [x] Debounce input for 300 ms; abort superseded requests and ignore late results.
- [x] Ignore empty queries and restore the normal sidebar immediately when search is cleared.
- [x] Preserve the current conversation while typing; use keyboard-accessible result buttons.
- [x] Support Escape to clear search and an explicitly labeled Clear search button.
- [x] Keep SQLite search simple, with no vector database or premature FTS dependency.

The flow is **Search input → FastAPI → SQLite → matching titles/messages →
search results → open conversation**. Results are ordered by recent message
activity and capped at 100 conversations; the UI asks for a narrower query when
more matches exist. Queries are limited to 200 characters. Search scans stored
text; SQLite FTS is a possible future optimization if history grows large.

## API

| Method | Endpoint | Purpose |
| --- | --- | --- |
| POST | `/conversations` | Create a chat (the UI does this only on the first send) |
| GET | `/conversations` | List chats by `updated_at DESC, id DESC` |
| GET | `/conversations/{id}/messages` | Load isolated history; `?include_ids=true` adds IDs for search navigation |
| PATCH | `/conversations/{id}` | Rename with `{ "title": "My title" }` |
| DELETE | `/conversations/{id}` | Delete a conversation and cascade its messages |
| GET | `/search?q=words` | Search saved titles and messages |
| POST | `/chat` | Stream a reply to `{ "conversation_id": 1, "message": "Hello" }` |

`title_matches` and `snippet_matches` in search results contain half-open
Unicode character ranges. The frontend slices `Array.from(text)` to preserve
those offsets. Message IDs are only UI metadata; the model receives `role` and
`content` from the selected conversation.

## Verify

Run from the project root after installing development dependencies:

```powershell
.\.venv\Scripts\python.exe -B "Stage 6(Better conversation management and Chat search)/tests/check_database.py"
.\.venv\Scripts\python.exe -B "Stage 6(Better conversation management and Chat search)/tests/check_browser.py"
.\.venv\Scripts\python.exe -B "Stage 6(Better conversation management and Chat search)/tests/check_reload.py"
```

The database and browser checks use isolated temporary databases and mocked
Ollama responses. Browser checks use installed Edge by default; pass `--browser`
with an Edge or Chromium executable path if necessary. They exercise desktop
and mobile layouts, streaming and Markdown, conversation isolation, title
generation/fallback, rename/delete, cascade cleanup, date grouping, search,
Unicode highlights, stale responses, and matching-message navigation.

With Ollama running, `tests/check_live.py` additionally checks real model replies
and persistence across a server restart. Tests never open the production database.
