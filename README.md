# LLM chat wrapper tutorial

Each stage is a separate FastAPI app. The feature audit covers the following lessons:

| Stage | Lessons | Status |
| --- | --- | --- |
| Stage 1 | Lesson 2 — First working LLM wrapper | Complete |
| Stage 2 | Lessons 3–4 — Conversation history and system instructions | Complete |
| Stage 3 | Lessons 5–6 — Streaming and chat UI | Complete |
| Stage 4 | Lessons 7–8 — Markdown and localStorage persistence | Complete |
| Stage 5 | Lesson 9 — SQLite conversation persistence | Complete |

## Lesson 1 — Architecture

- [x] Frontend vs backend vs LLM
- [x] Browser → backend → LLM → backend → browser flow
- [x] Why an LLM wrapper is more than just the model

The browser draws the UI, handles input, and sends HTTP requests. FastAPI
receives those requests and calls Ollama, which runs the model. The wrapper
also controls conversation history, system instructions, streaming, rendering,
and saving chats.

## Lesson 2 — First working LLM wrapper — Complete in Stage 1

- [x] Basic HTML frontend
- [x] FastAPI backend
- [x] Ollama connection
- [x] LLM model connection (`llama3.2:3b`)
- [x] `POST /chat` endpoint
- [x] Send a user message to the LLM
- [x] Return the LLM response to the browser

`index.html` sends `{ "message": "Hello" }`. `main.py` calls `ollama.chat()`
and returns `{ "reply": "..." }`. The browser displays that complete reply.

## Lesson 3 — Conversation history — Complete in Stage 2

- [x] User messages
- [x] Assistant messages
- [x] `messages[]` conversation array
- [x] Send the entire conversation to the backend
- [x] Multi-turn conversations
- [x] Follow-up questions
- [x] Verified conversation history reaches Ollama

The browser records each turn as `{ role, content }`, then sends the full
array with the next request. The model can use earlier turns because those
messages are included again; it is not independently remembering the user.

Live Ollama verification used the name **XYZ** and a **weather dashboard**
project. The follow-up answer correctly recalled both. When asked whether
XYZ was the assistant's name or the user's, it correctly identified the user.

## Lesson 4 — System prompt / behavior control — Complete in Stage 2

- [x] Added the system role
- [x] Added instructions before conversation history
- [x] Teach the model to use previous information
- [x] Distinguish user information from assistant information
- [x] Explain system instructions vs conversation history
- [x] Tested system-prompt behavior successfully
- [x] Generalized the prompt beyond a name-memory example

The backend prepends a system message containing behavior instructions.
The following user/assistant messages contain the actual conversation.
The system prompt asks the model to use history, understand follow-ups,
separate user facts from assistant facts, and give useful answers.

## Lesson 5 — Streaming — Complete in Stage 3

- [x] Ollama `stream=True`
- [x] FastAPI `StreamingResponse`
- [x] Python generator
- [x] Use `yield` to produce chunks
- [x] Stream model output from Ollama → FastAPI
- [x] Stream FastAPI output → browser
- [x] Browser `ReadableStream` reader
- [x] `TextDecoder`
- [x] Display the response while it is being generated
- [x] Accumulate streamed chunks into one response
- [x] Save the complete assistant response after streaming finishes

`generate()` yields each text chunk to `StreamingResponse`. In the browser,
`getReader()` reads incoming bytes and `TextDecoder` converts them to text.
`reply` accumulates the chunks and updates the screen. The assistant turn is
added to history only after the response finishes.

## Lesson 6 — Better chat UI — Complete in Stage 3

- [x] ChatGPT-style page layout
- [x] Sidebar
- [x] Main conversation area
- [x] Header
- [x] User message bubbles
- [x] Assistant response area
- [x] Bottom message composer
- [x] Textarea instead of a single-line input
- [x] Enter sends a message
- [x] Shift + Enter creates a new line
- [x] Automatic scrolling
- [x] Disable Send during generation
- [x] Basic error handling
- [x] Mobile-responsive layout
- [x] Safer DOM creation instead of inserting user text with `innerHTML`

On mobile, the menu button opens the sidebar. Output scrolls into view unless
you scroll up to read earlier messages. Stage 3 keeps history in memory, so
refreshing the page or choosing **New chat** clears it.

Stage 3's HTML, CSS, and JavaScript are in `static/index.html`,
`static/style.css`, and `static/script.js`. Paths in the backend are resolved
relative to `main.py`, so launching from the project root also works.

## Lesson 7 — Markdown — Complete in Stage 4

- [x] Chose Marked
- [x] Chose DOMPurify
- [x] Explain Markdown → HTML flow
- [x] Explain why generated HTML must be sanitized
- [x] Added Marked to `index.html`
- [x] Added DOMPurify to `index.html`
- [x] `marked.parse()` is used during streaming
- [x] `DOMPurify.sanitize()` runs before inserting generated content into the DOM
- [x] Markdown continues rendering while the response streams
- [x] Styling for headings
- [x] Styling for lists
- [x] Styling for inline code
- [x] Styling for code blocks
- [x] Styling for blockquotes
- [x] Styling for links
- [x] Styling for tables
- [x] System prompt encourages useful Markdown
- [x] Tested headings, bold, lists, tables, and code blocks

[Marked](https://marked.js.org/) converts the accumulated Markdown reply into
HTML. [DOMPurify](https://github.com/cure53/DOMPurify) removes disallowed
elements and attributes before anything is displayed. The code inserts a
sanitized DOM fragment with `replaceChildren()` instead of assigning raw
HTML to `innerHTML`. User messages and errors remain literal text.

Model replies and saved Markdown can contain unsafe HTML, event handlers, or
links. Parsing Markdown alone does not make that content safe; sanitization
removes disallowed content before the browser can interpret it as page markup.

Reparsing the accumulated reply allows formatting to span multiple chunks.
The original Markdown stays in conversation history. If a library fails
to load, replies safely fall back to plain text.

Pinned browser libraries and their licenses are included in `static/vendor/`,
so formatting needs no CDN connection at runtime. Wide code blocks and
tables scroll horizontally within the conversation.

## Lesson 8 — Persistent chats with localStorage — Complete in Stage 4

- [x] Explain why `messages[]` disappears after refresh
- [x] Use browser `localStorage`
- [x] Use `JSON.stringify()`
- [x] Use `JSON.parse()`
- [x] Load saved messages when the page opens
- [x] Implement `saveMessages()`
- [x] Save user messages to localStorage
- [x] Save completed assistant messages to localStorage
- [x] Redraw stored user messages
- [x] Redraw stored assistant messages
- [x] Preserve Markdown when restoring assistant messages
- [x] Implement `loadConversation()`
- [x] Implement New chat behavior

`messages` is a JavaScript array in page memory. Refreshing creates a new
page and a new array. `localStorage` retains string values across reloads.
`JSON.stringify(messages)` converts the array to a string for saving;
`JSON.parse(saved)` turns that string back into objects when the page opens.

Stage 4 saves **one current conversation** under
`llm-wrapper.stage4.messages.v1`. Each sent user message is saved immediately.
The assistant's original Markdown is saved only when generation completes.
`loadConversation()` validates the stored data, redraws user text, sanitizes
and renders assistant Markdown, and restores the conversation title.

**New chat** clears the current messages and removes only Stage 4's saved
conversation key. If you refresh during generation, the last unanswered
message returns to the input as a draft ready to resend. Failed requests
are removed from saved history and remain available to retry in the input.
Damaged, blocked, or full storage produces a notice without breaking chat.

Saved history belongs to this browser and address. Use the same protocol,
hostname, and port when returning to it: `localhost:8000` and
`127.0.0.1:8000` have separate storage. Starting a new chat intentionally
replaces the current saved conversation; the sidebar shows that one chat.

## Lesson 9 — SQLite conversation persistence — Complete in Stage 5

Stage 5 builds on the streaming and Markdown UI, replacing browser storage
with one conversation stored by Python's built-in `sqlite3` module.

### Lesson 9 checklist

The implementation items below are present in Stage 5. The explanations that
follow cover the learning items; a code audit cannot verify personal understanding.

- [x] Choose SQLite for backend persistence.
- [x] Explain why SQLite fits backend persistence better than `localStorage`.
- [x] Use Python's built-in `sqlite3`, without an ORM.
- [x] Store the database on D: inside the confirmed project directory.
- [x] Derive the path from `main.py` with `pathlib.Path`, independently of the
  current PowerShell directory.
- [x] Automatically create `data/` and initialize the database at startup.
- [x] Create `messages` with `id INTEGER PRIMARY KEY AUTOINCREMENT`,
  `role TEXT NOT NULL`, `content TEXT NOT NULL`, and
  `created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP`.
- [x] Implement `init_db()`, `save_message()`, `load_messages()`, and `clear_messages()`.
- [x] Use parameterized SQL with `?` placeholders and explain why interpolation is unsafe.
- [x] Make the backend and SQLite the source of truth for conversation history.
- [x] Remove browser-owned persistent history and competing `localStorage` logic.
- [x] Send only the newest user message and accept it with `ChatRequest.message: str`.
- [x] Save the user message, load full ordered history, prepend the system prompt,
  and send that history to Ollama.
- [x] Preserve `stream=True`, `StreamingResponse`, and progressive browser rendering.
- [x] Accumulate the assistant reply and save one row only after successful completion.
- [x] Implement `GET /messages` and restore the saved conversation on browser startup.
- [x] Implement `DELETE /messages` and connect New chat to backend deletion.
- [x] Restore assistant Markdown through Marked and DOMPurify; keep user text literal.
- [x] Preserve the existing UI, keyboard controls, scrolling, and streaming architecture.
- [x] Declare API routes before the static-file mount.
- [x] Record the detailed [Lesson 9 implementation prompt](Stage%205%28Creating%20a%20database%20for%20UI%29/LESSON_9_PROMPT.md).

### Why SQLite and SQL parameters?

`localStorage` belongs to a browser profile and web origin. FastAPI cannot read
it directly: the browser would have to send that history on each request.
Clearing browser storage also removes that browser's saved copy.

SQLite keeps history in a file controlled by the backend. Every browser window
using this Stage 5 server loads the same saved conversation, and closing a
browser or restarting the server does not delete the database. SQL lets the
backend load rows in order and commit writes as transactions. This makes SQLite
suitable for this lesson's backend persistence; `localStorage` can still be
useful for browser preferences. SQLite does not automatically provide user
accounts or separate conversations: this lesson has one shared conversation.

There is no ORM such as SQLAlchemy. The helpers call `sqlite3.connect()` and
execute SQL directly. For example, `save_message()` uses:

```python
connection.execute(
    "INSERT INTO messages (role, content) VALUES (?, ?)",
    (role, content),
)
```

The SQL command and message values are supplied separately. SQLite treats a
message containing quotes or SQL-looking text as data, rather than as part of
the command. Building this query with an f-string or string concatenation could
break its syntax or let user-controlled text change the query (SQL injection).

### How Stage 5 implements it

- `init_db()` creates the directory and `messages` table at server startup.
- `save_message()` uses SQL parameters; `load_messages()` orders by increasing
  message ID; `clear_messages()` deletes the stored conversation.
- Every connection is closed after use. Writes commit before returning.
- `POST /chat` accepts only `{ "message": "..." }`. The backend saves the
  user message, loads SQLite history, and prepends the existing system prompt.
- Ollama uses `llama3.2:3b` with `stream=True`. The browser renders
  chunks immediately; one complete assistant row is saved before stream EOF.
- `GET /messages` restores the conversation after refresh or server restart.
  Assistant Markdown is parsed with Marked and sanitized with DOMPurify again.
- **New chat** calls `DELETE /messages` and clears the page only after success.
  This permanently deletes the one conversation; multiple chats come later.
- Stage 5 no longer reads or writes chat history in `localStorage`.
  Existing Stage 4 browser saves are left alone and are not imported.

The database location is derived from `main.py`, not the terminal's working
directory: `BASE_DIR.parent / "data" / "chat.db"`. In this project that is
`D:\Projects\LLM chat wrapper tutorial\data\chat.db`, the confirmed location.
Its absolute path is printed once at startup. The database itself is not served
as a static file. No database is created in the browser profile or temporary
directory, and changing the terminal's directory does not change this path.

The request flow is:

```text
Browser sends newest message
  → FastAPI saves user row in SQLite
  → loads ordered history and prepends system instructions
  → Ollama generates a streamed response
  → FastAPI yields chunks; browser displays sanitized Markdown
  → FastAPI saves the completed assistant response as one row
```

If generation fails or is interrupted, the saved user message remains, but
partial assistant text is not saved. The page reloads server history on an
error instead of guessing what was committed or automatically resending it.
Run this single-conversation lesson with **one Uvicorn worker**: a lock rejects
overlapping sends/deletes with HTTP 409 while a reply is in progress.

From the project root, with Ollama running and `llama3.2:3b` installed:

```powershell
.\.venv\Scripts\python.exe "Stage 5(Creating a database for UI)/run.py"
```

Open http://127.0.0.1:8000. If the model is missing, install it with
`ollama pull llama3.2:3b` before sending a message.

The launcher restarts the server when an application file changes. See
[Automatic server restarts in Stage 5](#automatic-server-restarts-in-stage-5)
for the implementation, watched files, and setup instructions.

Run Stage 5's database, browser, and restart checks with:

```powershell
.\.venv\Scripts\python.exe -B "Stage 5(Creating a database for UI)/tests/check_database.py"
.\.venv\Scripts\python.exe -B "Stage 5(Creating a database for UI)/tests/check_browser.py"
.\.venv\Scripts\python.exe -B "Stage 5(Creating a database for UI)/tests/check_reload.py"
```

The database and browser checks use real SQLite files and controlled model responses. The browser checks
use installed Edge, the real FastAPI endpoints, and the existing page. Test
databases and browser profiles are isolated under unique project `data/`
subdirectories and removed afterwards; your saved conversation is not cleared.
The restart check uses a separate tiny app and verifies that each watched file
produces a new server process while unrelated file changes leave it running.

An optional test uses the actual configured model, stops and restarts a real
Uvicorn process, and verifies preference recall before and after the restart:

```powershell
.\.venv\Scripts\python.exe -B "Stage 5(Creating a database for UI)/tests/check_live.py"
```

This also uses an isolated database and requires Ollama to be running.

Verified on September 30, 2026:

- All 14 database/API tests passed, including parameterized SQL, path stability,
  completed-only assistant saves, invalid requests, and disconnect/error cleanup.
- Desktop and mobile browser tests passed for progressive Markdown, sanitization,
  refresh restoration, full database context, New chat, keyboard controls,
  scrolling, and failed load/send/delete recovery.
- Live `llama3.2:3b` recalled **Python** as the preferred language before and
  after an actual Uvicorn restart. The first reply arrived in 140 chunks.
  Deleting messages then produced a fresh conversation with no old context.

## Automatic server restarts in Stage 5

Stage 5 uses a development launcher, `run.py`, to restart the server when one
of the files used by the application is saved. The FastAPI routes and SQLite
helpers remain in `main.py`; the launcher controls watching and restarting.

### 1. Install the file watcher

The launcher uses `watchfiles==1.3.0`, recorded in Stage 5's
`requirements-dev.txt`. It is already installed in this project's `.venv`.
For a new environment, run this from the project root:

```powershell
uv pip install --python .\.venv\Scripts\python.exe -r "Stage 5(Creating a database for UI)/requirements-dev.txt"
```

### 2. List the files the application uses

`RUNTIME_FILES` in `run.py` explicitly lists the six current application files:

```python
RUNTIME_FILES = (
    "main.py",
    "static/index.html",
    "static/style.css",
    "static/script.js",
    "static/vendor/marked-18.0.14.umd.js",
    "static/vendor/dompurify-3.4.16.min.js",
)
```

This includes the backend, page, stylesheet, chat JavaScript, and the two
libraries used to render and sanitize Markdown. It does not automatically
discover dependencies: the explicit list determines which changes count.

### 3. Filter changes using absolute paths

The launcher calculates its own directory and resolves each listed file:

```python
BASE_DIR = Path(__file__).resolve().parent
WATCHED_PATHS = frozenset((BASE_DIR / file).resolve() for file in RUNTIME_FILES)

def application_file_changed(change, path):
    return Path(path).resolve() in WATCHED_PATHS
```

`watchfiles` watches the Stage 5 directory and passes file-change events to
`application_file_changed()`. The function returns `True` only when the changed
file is in `WATCHED_PATHS`. Absolute paths keep this decision independent of
the PowerShell directory used to launch the app.

| Changed file | Server restarts? |
| --- | --- |
| Any of the six files in `RUNTIME_FILES` | Yes |
| `README.md`, lesson prompts, or library licenses | No |
| Test scripts or unused files | No |
| `data/chat.db` or Python cache files | No |
| Files in another stage | No |

Database writes must not trigger restarts: saving each chat message changes
`chat.db`. Excluding it lets the conversation continue while messages are saved.

### 4. Print a warning and restart the server process

The launcher connects the file filter to `watchfiles.run_process()`:

```python
run_process(
    BASE_DIR,
    target=serve,
    args=(options.port,),
    watch_filter=application_file_changed,
    callback=report_changes,
)
```

`run_process()` starts `serve()` in a child process. When a matching change
arrives, `report_changes()` prints a warning such as:

```text
WARNING: Changes detected in static\style.css. Restarting server...
```

The watcher then stops that server process and starts a new one. `serve()`
starts Uvicorn with `main:app`, using port 8000 by default. It also adds the
Stage 5 directory to Python's import path so `main` resolves to this stage.
Uvicorn is started without `--reload` because `watchfiles` already manages
restarts; running both would create two reload mechanisms.

The `if __name__ == "__main__":` guard starts the launcher only when `run.py`
is executed directly. This prevents a spawned process importing the file
from accidentally starting another launcher.

### 5. Run it and see your changes

Stop an existing server with **Ctrl+C**, then run from the project root:

```powershell
.\.venv\Scripts\python.exe "Stage 5(Creating a database for UI)/run.py"
```

Open http://127.0.0.1:8000. Saving a listed file triggers the warning and server
restart. The older `uvicorn main:app --reload` command does not use this list.

Restarting the server does not automatically refresh the browser. Refresh the
page after frontend changes; use **Ctrl+F5** if cached files are still displayed.
Saved SQLite messages survive a server restart.

When adding a new application file, add its path relative to Stage 5 to
`RUNTIME_FILES`. Stop and start `run.py` again to load the updated list.
Changes to `run.py` itself require this manual restart because it is the
launcher, not one of the watched application files.

### How this was verified

`tests/check_reload.py` launches a separate tiny app and edits each of the six
watched files. It checks that each edit produces a new server process, then
checks that changes to excluded files leave the same process running. It also
checks that the file filter works from different launch directories. These
checks passed without changing the real chat database.

```powershell
.\.venv\Scripts\python.exe -B "Stage 5(Creating a database for UI)/tests/check_reload.py"
```

## Stage folders

```text
Stage 1(Connecting a LLM to a web UI)/
├── main.py
└── index.html

Stage 2(Giving LLM chat history,system prompt and behaviour control)/
├── main.py
└── index.html

Stage 3(Adding streaming to the conversation and better UI)/
├── main.py
└── static/
    ├── index.html
    ├── style.css
    └── script.js

Stage 4(Markdown and persistent chats)/
├── main.py
├── static/
│   ├── index.html
│   ├── style.css
│   ├── script.js
│   └── vendor/
└── tests/
    ├── check_markdown.py
    ├── markdown_checks.js
    └── persistence_checks.js

Stage 5(Creating a database for UI)/
├── main.py
├── run.py
├── requirements-dev.txt
├── LESSON_9_PROMPT.md
├── static/
│   ├── index.html
│   ├── style.css
│   ├── script.js
│   └── vendor/
└── tests/
    ├── check_database.py
    ├── check_browser.py
    ├── browser_checks.js
    ├── check_reload.py
    └── check_live.py

data/
└── chat.db  (Stage 5's SQLite conversation)
```

## Run a stage

Keep Ollama running with `llama3.2:3b` installed. From the project root in
PowerShell, run Stage 4 with:

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --app-dir "Stage 4(Markdown and persistent chats)"
```

For Stages 1–4, replace the quoted directory with the desired stage's folder name.
For Stage 5, use the `run.py` command in Lesson 9 above to enable its application-file watch list.
Stop the previous server with Ctrl+C before using the same port.
Open http://127.0.0.1:8000. Use Ctrl+F5 after HTML, CSS, or JavaScript changes.

Try asking:

> Show a Markdown example with a heading, bold text, a bulleted list,
> a comparison table, and a fenced Python code block.

Then refresh Stage 4 and verify that the conversation and formatting return.

## Verification

The audit ran live Ollama smoke checks for Stage 1 and Stage 2. They verified
a greeting, name/project recall, and separation of user and assistant identity.
The repeatable regression suite uses deterministic model responses.

From the project root, run the complete audit:

```powershell
.\.venv\Scripts\python.exe -B tests/check_lessons.py
```

Or run only Stage 4's Markdown and persistence checks:

```powershell
.\.venv\Scripts\python.exe -B "Stage 4(Markdown and persistent chats)/tests/check_markdown.py"
```

These checks use installed Microsoft Edge in headless mode and do not require
Ollama. Pass `--browser "path/to/browser.exe"` for another Edge/Chromium location.
Each run uses temporary browser profiles rather than your saved conversations.

The full suite passed checks for:

- Stage 1 requests and response display.
- Stage 2 complete history, system instructions, and follow-up requests.
- Stage 3 streamed chunks, keyboard controls, scrolling, errors, safe DOM, and mobile layout.
- Stage 4 Markdown formats, sanitization, raw Markdown history, and interrupted streams.
- Stage 4 immediate saves, completed replies, actual page reloads, restored Markdown,
  New chat clearing, invalid/blocked/full storage, and interrupted-request recovery.
