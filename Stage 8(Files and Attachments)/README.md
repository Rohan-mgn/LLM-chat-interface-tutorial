# Stage 8 — Files and attachments (Lesson 17)

Stage 8 copies Stage 7 and adds Lesson 17. It runs independently and stores
SQLite history at `data/stage8/chat.db`. Stage 7's code and data stay separate.
Automatic titles, rename/delete menus, search, date grouping, isolated context,
streaming, and sanitized Markdown remain available.

## Run

From the project root in PowerShell, with Ollama running:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r "Stage 8(Files and Attachments)/requirements-dev.txt"
ollama pull llama3.2:3b
.\.venv\Scripts\python.exe "Stage 8(Files and Attachments)/run.py" --port 8002
```

Open `http://127.0.0.1:8002`. The optional port avoids conflicting with Stage 6
and Stage 7 on their own ports. Use one worker: the generation/deletion lock is local to the process.
The launcher watches runtime files, including `tree_store.py` and `static/composer.js`.
If an IDE Run button uses system Python without the app dependencies, `run.py`
automatically relaunches with the project's existing `.venv`, preserving the
port argument. If dependencies are still missing, it prints an installation
command instead of failing on an import. For IDE import diagnostics, select
`.venv/Scripts/python.exe` as the Python interpreter.
The repository and Stage 8 each include VS Code settings for the shared `.venv`,
so either folder can be opened as the workspace. On Windows, their Pylance
import paths explicitly include that environment's `Lib/site-packages`. If old
unresolved-import warnings remain, run **Developer: Reload Window** from the
Command Palette. These settings locate installed packages; they do not install
dependencies or suppress import diagnostics.

## Lesson 17 — Files and attachments

The flow in this lesson is **upload → store → associate → display/download**.
Files are not added to prompts, sent to Ollama as images, parsed into document
text, embedded, or retrieved. Sending files without text saves the user turn
and returns a local explanation that file contents cannot be read yet. A normal
text message with attachments sends only its text and selected conversation
history to Ollama. The UI states this boundary beside the composer.

The lesson progression remains:

| Lesson | Topic |
| --- | --- |
| 11 | Better conversation management |
| 12 | Chat search |
| 13 | Smart composer |
| 14 | Message actions |
| 15 | Long-response UX |
| 16 | Advanced code rendering |
| 17 | Files / attachments — this addition |

This stage adds the requested Lesson 17 features on top of Stage 7; it does not
claim that dedicated Lessons 15–16 implementations have been added.

### Using attachments

- Use **Attach files** to open the native multiple-file picker.
- Drop files onto the composer, or paste files/images when the browser exposes
  them through the clipboard. Ordinary text paste still works normally.
- Cards show filename, type, size, upload percentage, validation, success, or
  failure. Successful images receive safe thumbnail previews.
- **Remove** cancels/removes a draft upload. **Retry** repeats a failed upload
  with the same UUID, so a lost success response cannot create a duplicate.
- Send stays disabled while uploads are pending, failing, being removed, or
  while saved attachment metadata is loading. Text editing remains available
  during uploads. Remove or successfully retry a failed file to enable Send.
- Sent attachments appear on their user message with download links. Missing
  files or failed previews get a visible unavailable state.
- Pending uploads remain scoped to their conversation across switches/reloads.
  A selected local file that failed before being stored must be picked again
  after a reload; browsers do not persist arbitrary local File objects.
- Editing a user message preserves its attachments on both sibling branches.
  Regeneration keeps the original parent user and its attachment links. The
  inline editor changes text; replacing a sent attachment requires a new send.

### Formats and limits

| Limit | Value |
| --- | --- |
| Files per message / pending conversation draft | 5 |
| Each file | 10 MiB (10,485,760 bytes) |
| Combined files per message / pending draft | 25 MiB |
| Image dimensions | At most 20 million pixels |
| Preview dimensions | At most 512 × 512 pixels |
| Abandoned, unsent uploads | Expire after 24 hours |

Supported extensions: `.txt`, `.md`, `.csv`, `.pdf`, `.png`, `.jpg`, `.jpeg`,
`.webp`. DOCX is intentionally deferred until a later parser lesson. Empty files
are rejected. TXT/MD/CSV must be UTF-8 (a BOM is accepted), and disallowed control
bytes or obvious binary signatures are rejected. CSV structure is not parsed.

Validation checks extension, declared MIME where useful, and actual bytes.
Generic/missing MIME hints are accepted only after content validation. PDFs
must have a PDF header and EOF marker; this is a format check, not full document
parsing or a malware scan. Pillow verifies image format, dimensions, and
complete decoding before a preview is created. Original image metadata is not
copied to the re-encoded PNG preview. Originals are always served as downloads,
never inserted as executable markup. SVG/HTML/executables are not supported file
formats. Text is displayed using DOM text nodes, including hostile filenames.

### Storage and database relationships

All persistent storage is derived from `Path(__file__)`, not the terminal's
working directory. In this checkout it lives on D: without opening Stage 7's DB:

```text
D:\Projects\LLM-chat-interface-tutorial\data\stage8\
  chat.db
  uploads/
    <uuid>.blob
    <uuid>.preview.png
```

User filenames are reduced to a sanitized display basename. They never become
storage paths. UUID storage keys are validated, path containment is checked,
and symlink targets are rejected. No raw storage path appears in the API.

`files` stores ID, conversation ID, original filename, internal storage key,
verified MIME, byte size, optional preview key, and creation timestamp.
`message_files(message_id, file_id, conversation_id)` links files to user
messages. Composite foreign keys prohibit cross-conversation associations.
This join table permits branches to share a blob without copying bytes or
changing the original message's ownership. A normal send cannot reuse a file
already bound to another user message; edit inheritance is explicit.

The new user message and all its file links commit in one transaction. Uploads
are first written to uniquely named partial files, validated, then renamed and
registered. A database failure removes the newly moved blob; a crash between
rename and commit is reconciled on startup. Files have no global public upload
mount: downloads and previews require both conversation ID and file UUID.
Responses set Content-Disposition, `nosniff`, a restrictive CSP, and no-store.
This remains a local single-user app, not an authenticated multi-user service.

### Cancellation and deletion policy

- Aborting the picker performs no upload. Removing an in-flight upload aborts
  XHR and records a cancellation UUID on the backend. A late upload cannot
  recreate a removed file. Upload retries are idempotent.
- A bounded ASGI middleware rejects oversized bodies before multipart parsing,
  including chunked bodies without Content-Length. File-size checks also run
  while writing. Bounded multipart data stays in memory; durable storage is
  inside `data/stage8/uploads`, not a global temporary upload directory.
- Interrupted/invalid uploads remove partial files and previews. If a browser
  disappears after the backend accepts an upload, it becomes a pending upload
  and expires after 24 hours unless sent.
- Pending file removal deletes its DB row and schedules physical deletion.
  Removing a sent file directly is rejected so existing branches remain intact.
- Conversation deletion cascades across all branches and attachment links,
  then removes originals and previews. If a message is deleted by storage code,
  its links are removed; a shared file is deleted only after its final link goes.
- SQLite triggers record physical deletion work in `file_deletions` as part of
  the same transaction. Failed disk deletions (for example an open Windows
  download) remain queued and retry every minute and at startup.
- Startup also removes recognized orphan blobs, previews, and partial files.
  Periodic cleanup expires abandoned pending files. It never scans outside the
  controlled upload directory. A missing historical blob keeps its metadata so
  the UI can explain that it is unavailable.

### Attachment API and files to study

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/attachment-limits` | Supported formats and size/count limits |
| POST | `/conversations/{id}/files/{uuid}` | One multipart `file` UploadFile; client generates a UUID for retries |
| GET | `/conversations/{id}/files` | Pending uploads for that conversation |
| GET | `/conversations/{id}/files/{uuid}` | Download original |
| GET | `/conversations/{id}/files/{uuid}?preview=true` | Safe re-encoded PNG preview |
| DELETE | `/conversations/{id}/files/{uuid}` | Remove/cancel an unsent upload |
| POST | `/chat` | Existing generation endpoint, now accepting `attachment_ids: [uuid, ...]` for sends |

Tree/history responses include an `attachments` array on every message. It
contains public file metadata, availability, and preview availability; bytes
are fetched separately. Upload processing is in `attachments.py`, HTTP/body
limits in `upload_routes.py`, and UI states in `static/attachments.js`.
`tree_store.py` binds files within the existing message-tree transaction.

FastAPI multipart support and image validation add two pinned dependencies:
`python-multipart` and `Pillow`. No document parser, embeddings library, vector
database, or RAG framework is added. A later lesson can build extraction,
chunking, embeddings, and retrieval on these stored file relationships.

### Attachment manual checks

- [ ] Pick several supported files; verify names, types, sizes, progress, and success.
- [ ] Drop a file and paste an image; verify ordinary text paste is unaffected.
- [ ] Remove an upload while in progress and after completion; verify it stays removed.
- [ ] Interrupt a connection, then Retry; verify no duplicate attachment.
- [ ] Try a disguised image, unsupported extension, empty file, oversized file, sixth file, and excessive combined size.
- [ ] Send text plus files and files alone; verify neither is silently analyzed as RAG.
- [ ] Switch conversations with pending uploads, return, and reload.
- [ ] Edit and regenerate a message with files; switch sibling branches and check attachments remain correct.
- [ ] Download originals and view image thumbnails; check mobile layout and keyboard controls.
- [ ] Delete a conversation with attachments; verify its files and previews disappear from disk.

The following sections document the inherited Stage 7 features.

## Lesson 13 — Complete

- [x] Textarea grows to 180 px, then scrolls internally, and shrinks when text is removed.
- [x] Physical-keyboard Enter sends ordinary prose; Shift+Enter always inserts a normal newline.
- [x] IME composition and the Enter ending composition never submit accidentally.
- [x] Enter continues `-`, `*`, `+`, `1.`, and `1)` lists, incrementing numbers.
- [x] Backspace or Enter on an empty list item removes the marker and retains indentation.
- [x] Nested indentation is preserved; Tab/Shift+Tab indent/unindent lists, code, and selected lines.
- [x] Backtick/tilde fences track marker length; Enter inside code preserves indentation.
- [x] Enter on an empty code line inserts a closing fence. Shift+Enter keeps a blank line inside code.
- [x] Mid-text edits and selections are respected; indentation preserves selected text.
- [x] Smart edits participate in native undo/redo.
- [x] Drafts are separate for every conversation and New Chat, including cursor position and selection.
- [x] Switching/reloading restores drafts; accepted sends clear submitted drafts, rejected sends retain them.
- [x] During generation, Copy, Edit, Regenerate, branch arrows, and Stop stay available; other conflicting actions wait.
- [x] Startup history loading leaves the composer editable; delayed responses cannot overwrite a new draft.
- [x] History and generation-status requests time out after 10 seconds instead of locking the composer indefinitely.
- [x] Ordinary Tab focus navigation and Ctrl/Meta/Alt shortcuts remain native.
- [x] Touch Return inserts newlines and continues lists/code; the visible Send button submits.
- [x] Touch-friendly Indent/Unindent buttons supplement keyboard shortcuts.
- [x] An empty command registry reserves a future slash-command extension point; `/text` is ordinary text today.

Drafts use this tab's `sessionStorage`, with an in-memory fallback. Closing the
tab can discard unsent drafts. SQLite owns saved messages. Inline editing leaves the normal composer draft untouched. Cancel (or Escape)
closes the inline editor without saving. Switching away discards an unsubmitted
inline edit; the normal per-conversation draft remains saved.

If the page appears stuck, verify that you opened the running Stage 8 server at
`http://127.0.0.1:8002` using the command above, then refresh the page. You can
type while history loads; sending becomes available when loading finishes or
reports an error. A hard refresh (`Ctrl+F5`) loads updated browser scripts.

Smart edits use plain-text `execCommand('insertText')` to retain the native undo
buffer. The API is deprecated; on a browser without support, ordinary typing is
left native instead of breaking edit history. See [MDN's editing and undo notes](https://developer.mozilla.org/en-US/docs/Web/API/Document/execCommand).

## Lesson 14 - Message actions and persistent branches

- [x] Copy user or assistant text, including raw Markdown, with temporary Copied feedback. Copy remains available during generation.
- [x] Edit every user message inline, with a prefilled textarea, Save and generate, and Cancel.
- [x] Cancel and unchanged submissions create no messages. Normal composer drafts survive editing.
- [x] Changed submissions add a sibling user message and its new assistant response in the same conversation.
- [x] Regenerate adds an assistant sibling under the exact original user message.
- [x] Retry of a failed response also adds a sibling; the failed text remains available.
- [x] Previous/next version buttons appear wherever messages share a parent, including root user messages.
- [x] Each sibling retains its own descendants and selected later versions, even after reload/restart.
- [x] Edit, Regenerate, or branch switching during generation automatically cancel the old response first.
- [x] Stop preserves partial Markdown as `stopped`; errors preserve partial text as `error`.
- [x] Generation IDs and active-view checks reject stale streamed chunks.
- [x] Feedback remains local, persistent metadata; it is never sent to the model.
- [x] Search includes hidden branches and opens the ancestry of the matching message.
- [x] Keyboard labels, visible focus, and touch targets support the message controls.

### How the message tree works

`tree_store.py` owns SQLite migration, message creation, ancestry, and branch
selection. `main.py` owns validation, HTTP endpoints, Ollama streaming, and
cancellation. `static/script.js` owns the selected view and interaction state.
The smart composer and Markdown renderer remain separate from tree storage.

Every message has `id`, `conversation_id`, `parent_id`, `role`, `content`,
`status`, and `created_at`. A null parent means a root; otherwise the parent is
the immediately preceding message in that branch. Status is one of
`generating`, `completed`, `stopped`, or `error`. Existing feedback and generation
metadata remain available. The older `reply_to_message_id` field is retained
for compatibility and equals an assistant's parent user ID for new messages.

```text
User A
  +-- Assistant A1
  |     +-- User B
  |           +-- Assistant B1
  +-- Assistant A2
        +-- User C
              +-- Assistant C1
```

Editing User B inserts another user with the same `parent_id` (Assistant A1).
It leaves User B, Assistant B1, and all their descendants unchanged. The new
user and new response become selected. Regenerating Assistant A1 instead
inserts an assistant with User A as its parent, leaving A1 and its descendants
intact. No conversation or shared prefix is copied. An unchanged edit is a
frontend no-op; direct unchanged API edits return 409 without inserting rows.

`branch_selections` stores a selected child for each `(conversation_id,
parent_key)`; parent key `0` denotes roots. Selecting a message updates choices
along its ancestry, retaining choices below other siblings. The frontend's
`getChildren`, `getSiblings`, and `getActiveConversationPath` start at the root
and follow those choices to a leaf. The first child is the default where no
choice has been saved. Thus selecting A2 shows C/C1; returning to A1 restores
B/B1. Sibling order follows insertion ID. Choices are persisted per conversation
and shared by tabs, rather than having independent branch selections per tab.

A new normal message attaches to the displayed leaf. The backend constructs
model context by walking **that request's parent chain**, never by selecting all
messages in a conversation or ordering branches into a flat history. Only
completed messages enter model context. Stopped/error assistant text remains
visible and can be regenerated or followed by a new user turn, but that partial
assistant text is omitted from subsequent model input.

### Cancellation and race protection

There is one active frontend generation with its own UUID and `AbortController`.
Stop aborts the streaming fetch immediately and sends a separate
`POST /generations/{uuid}/stop`. Edit, Regenerate, and branch arrows use the same
cancellation function, then wait for the old run's cleanup before editing,
creating another sibling, or loading another path. If cleanup cannot be
confirmed, the queued action is not performed and the page shows an error.

The backend cancels the async producer, closes the Ollama stream and client,
saves accumulated text with `stopped`, and releases the single-worker lock.
A Stop arriving just after reservation waits for the producer to enter its
cleanup-protected lifetime before cancelling; cancelling an unstarted asyncio
task would otherwise skip its `finally` block and strand the lock.
A browser disconnect also triggers this cleanup. If the response has already
finished and entered final cleanup, Stop waits and preserves its final status.
The application closes its connection; actual release of model-server compute
depends on Ollama's handling of that disconnect.

Each SSE event carries the generation UUID. Before rendering a token, the
frontend checks that UUID, the active generation object, its view version, and
whether cancellation was requested. It checks again after asynchronous loads.
Buffered tokens from an aborted fetch therefore cannot update a new branch.
Rapid action clicks use an intent counter: while cancellation is pending, only
the latest requested transition runs. SQLite records accepted generation UUIDs
so replayed requests cannot duplicate rows. Cancellation also records UUIDs
that have not yet arrived, preventing a delayed POST from starting after Stop.

Checkpoints are saved at most every 250 ms while streaming. Normal cancellation
saves all text received by the producer. After a process crash, startup marks
remaining `generating` rows `stopped` and retains their last saved checkpoint.

### Migration and data integrity

On first startup after this update, each existing flat history is linked in
place with parent pointers. IDs, content, conversation names, and old copied
conversations are preserved; old copied conversations are not guessed together
or merged. Their existing Open original chat link remains available. Legacy
`complete` statuses become `completed`. Repeated startup does not relink trees.

Foreign keys are enabled on every connection. Parent insertion is checked to
belong to the same conversation, and existing parent/conversation edges are
immutable. Branch reservations and selection changes are transactional.
Deleting a conversation deletes **all** its message branches, selections, and
accepted generation references through cascades. Confirmation still precedes
the destructive UI action. Cancellation UUID records contain no message text
and remain to reject late requests.

### API

`POST /chat` both reserves messages atomically and streams a response:

```json
{
  "conversation_id": 1,
  "generation_id": "00000000-0000-4000-8000-000000000001",
  "action": "send",
  "message": "Hello",
  "parent_id": null,
  "message_id": null
}
```

Use a fresh UUID for every action. For `send`, `parent_id` identifies the
selected assistant leaf (null starts a root); omission uses the server's active
leaf. For `edit`, pass a user `message_id` and changed nonblank `message`.
For `regenerate` or `retry`, pass the target assistant `message_id` (a user ID is
also accepted for recovery of a user turn without a response). The server
resolves the target's ancestry; a supplied `parent_id` does not relocate edits
or regenerations. IDs must belong to the requested conversation.

SSE events are `start`, `delta`, `error`, and `done`, all carrying `generation_id`.
`start` adds conversation/user/assistant IDs; the conversation ID does not change
when branching. `done` reports the saved status. JSON encoding prevents model
text from becoming a protocol event.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/conversations/{id}/tree` | All message nodes, `active_children`, and `active_path_ids` |
| PUT | `/conversations/{id}/branch` | Select ancestry of `{"message_id": 42}` and return updated tree |
| GET | `/conversations/{id}/messages?include_ids=true` | Selected path with IDs, parents, statuses, and metadata |
| POST | `/chat` | Create user/reply nodes or sibling branches and stream a response |
| GET | `/generations/{generation_id}` | Recover accepted message IDs after connection loss |
| POST | `/generations/{generation_id}/stop` | Cancel UUID and wait for persistence/cleanup |
| PATCH | `/conversations/{id}/messages/{message_id}/feedback` | Store `{"value": 1}`, `-1`, or `null` |

The existing conversation/title/search endpoints remain available. Branch
selection and another generation return 409 while a producer is active; API
clients must await cancellation first, as the UI does. Keep one Uvicorn worker
because active generation tasks and the lock belong to that process. This
learning app loads a conversation's tree into memory; very large histories may
later need paginated tree loading, without changing parent relationships.

## Manual test checklist

- [ ] **Normal generation:** send two messages; verify streaming, Markdown, and follow-up context. Reload and verify persistence.
- [ ] **Stop:** stop before any text and again after several tokens; verify the response remains with a Stopped label and partial text survives reload.
- [ ] **Edit completed user:** edit an earlier question, save, and verify a new sibling is selected with a new reply; the original remains accessible.
- [ ] **Edit during generation:** click Edit directly while tokens arrive; verify streaming stops, the old response is Stopped, and the inline editor opens. Save and verify a new branch.
- [ ] **Cancel/unchanged edit:** cancel, press Escape, and submit unchanged text; verify no extra version and the normal composer draft remains intact.
- [ ] **Regenerate:** regenerate a completed or stopped answer; verify the version count increases and older answers are unchanged.
- [ ] **Regenerate during generation:** click Regenerate while tokens arrive; verify the previous partial answer is Stopped and the new answer is a sibling under the same user.
- [ ] **User branch switching:** use the user's previous/next arrows; verify only the selected user's descendants appear.
- [ ] **Assistant branch switching:** use assistant arrows and reload; verify the selected answer and its descendants are restored.
- [ ] **Separate continuations:** continue on A1, switch to A2 and continue differently, then switch back and forth. Verify each retains its own later messages and model context.
- [ ] **Rapid actions:** while streaming, quickly click Edit then a branch arrow, or Regenerate then Edit. Verify the latest action wins, partial replies remain saved, and old tokens never appear in the new view.
- [ ] **Existing features:** copy a user query while streaming, search for text in a hidden branch, switch chats with drafts, and delete a chat containing multiple branches.

## Verify

```powershell
node "Stage 8(Files and Attachments)/tests/check_composer.js"
.\.venv\Scripts\python.exe -B "Stage 8(Files and Attachments)/tests/check_attachments.py"
.\.venv\Scripts\python.exe -B "Stage 8(Files and Attachments)/tests/check_database.py"
.\.venv\Scripts\python.exe -B "Stage 8(Files and Attachments)/tests/check_browser.py"
.\.venv\Scripts\python.exe -B "Stage 8(Files and Attachments)/tests/check_reload.py"
```

Tests use isolated SQLite databases and deterministic mocked model responses.
Browser checks exercise the actual app in headless Edge at desktop and mobile
widths; pass `--browser` to select another Chromium executable.
`tests/check_live.py` also checks real Ollama streaming, context isolation, and
persistence through a server restart. No test opens the production database.
