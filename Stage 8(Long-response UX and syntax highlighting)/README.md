# Stage 8 — Long-response UX and syntax highlighting

Stage 8 copies Stage 7's working chat, search, smart composer, message actions,
branching, cancellation, Markdown and SQLite implementation, then adds Lessons
15 and 16. Stage 7's files and database are unchanged. This stage does not
contain the previously removed file-attachment implementation.

## Run

From the project root, with Ollama running and llama3.2:3b available:

~~~powershell
.\.venv\Scripts\python.exe -m pip install -r "Stage 8(Long-response UX and syntax highlighting)/requirements-dev.txt"
.\.venv\Scripts\python.exe "Stage 8(Long-response UX and syntax highlighting)/run.py"
~~~

Open http://127.0.0.1:8000. Stop another server using that port first, or pass
--port 8001 to the launcher. Use Ctrl+F5 after frontend changes.

The independent database is data/stage8/chat.db, resolved from main.py.
It is created automatically on startup, independently of the terminal directory.
Existing Stage 7 chats are not copied or modified.

## Lesson 15 — Long-response UX

- Assistant replies have a **400px maximum height**. Short replies keep their
  natural height. Adjust --response-max-height in response-ui.css.
- Overflow is measured using scrollHeight versus clientHeight, with a one-pixel
  tolerance for rounding. Only overflowing replies receive scrolling controls,
  a focus target and the long-reply hint.
- The main conversation remains the primary mouse-wheel surface. Hovering over
  an overflowing response for **850ms** enables inner scrolling. Leaving early
  cancels the timer; leaving after activation deactivates it.
- Active responses use overflow-y: auto and overscroll-behavior-y: contain.
  Inactive vertical wheel gestures go to the main conversation. Horizontal and
  Shift+wheel gestures remain available for code and table scrolling.
- A subtle edge, helper text and thin scrollbar indicate the inner scroll area.
- Touch layouts allow direct inner scrolling without a hover delay. Touch input
  on hybrid devices also activates the response immediately.
- Tab focuses an overflowing reply or its scroll hint. Arrow keys, Page Up/Down,
  Home/End and Space use browser scrolling; Escape returns focus to the main
  conversation. Visible focus outlines are retained.
- A WeakMap attaches one controller per assistant container. Streaming updates
  reuse it; timers and ResizeObservers are cleaned up when changing chats.
  Resizing also rechecks overflow.
- The same assistant container survives streaming and final database
  reconciliation. Its vertical position and each code block's horizontal
  position are preserved. Completion does not steal focus from a reader.
- Main auto-follow pauses when the user scrolls upward or reads inside a reply.
  New streamed content then exposes **New content below**. **Latest response**
  explicitly jumps both the main view and the latest reply to the end.
- Markdown headings, lists, tables and code retain vertical reading order.
  Wide tables and long code lines scroll horizontally within the capped reply.
- No new scrolling animations are used. Reduced-motion preferences also disable
  smooth scrolling on these surfaces.

## Lesson 16 — Advanced code rendering

The locally bundled [Prism 1.30.0](https://github.com/PrismJS/prism/releases/tag/v1.30.0)
uses its [manual highlighting API](https://prismjs.com/docs/prism).
It needs no CDN requests or new Python packages.

- Included grammars: Python, JavaScript, CSS, HTML/XML, JSON, Bash and SQL, plus
  the C-like dependency. Aliases such as py, js, html and sh work.
- Fenced-code language labels come from Marked's code tokens. Unknown, missing
  or unsupported languages remain readable plain text.
- Every block has a language label and **Copy code**. Copy uses source text,
  never highlighted HTML or toolbar content. **Copied** resets after 1.8 seconds.
  If clipboard access fails, the source is selected for manual copying.
- Monospace code preserves indentation, tabs, blank lines and trailing spaces
  from Marked's parsed source. The renderer adds no extra newline. Standard
  Markdown parsing normalizes line endings.
- Long lines **do not wrap**; they scroll horizontally. There are deliberately
  no line numbers, keeping copying and small-screen layouts straightforward.
- Streaming shows intermediate plain code, including unfinished fences.
  Highlighting runs when a response settles or history is restored, not on every
  token. Unchanged results are cached per container.
- Blocks over 50,000 characters remain plain text to bound highlighting work.
  Missing grammars/libraries or highlighter errors also fall back to source;
  labels, copying and scrolling continue to work.
- Marked output passes through DOMPurify. Only code-language classes are retained.
  Prism output is separately sanitized to span/class markup before insertion.
  Neither path executes model HTML.
- Multiple blocks work independently. Toolbars sit outside the source, while
  surrounding lists and tables remain Markdown.
- The code surface uses a dark palette independent of the current light chat
  theme. CSS variables support future page themes. Set data-code-theme="light"
  on an ancestor to use the included light code palette. A full-page theme
  switch is outside these lessons.

## Files and reload

| File | Responsibility |
| --- | --- |
| static/response-ui.js | Sanitized rendering, code tools and reply scrolling |
| static/response-ui.css | Height limit, scroll hints, code surfaces and themes |
| static/script.js | Stream/final integration, reconciliation and latest button |
| static/vendor/prism-1.30.0.js | Pinned Prism bundle; MIT license and hashes alongside |
| run.py | Watches all new runtime files plus the inherited application files |

Database, test and documentation edits do not restart the server.

## Verification

~~~powershell
.\.venv\Scripts\python.exe -B "Stage 8(Long-response UX and syntax highlighting)/tests/check_database.py"
.\.venv\Scripts\python.exe -B "Stage 8(Long-response UX and syntax highlighting)/tests/check_browser.py"
.\.venv\Scripts\python.exe -B "Stage 8(Long-response UX and syntax highlighting)/tests/check_browser.py" --responses
.\.venv\Scripts\python.exe -B "Stage 8(Long-response UX and syntax highlighting)/tests/check_reload.py"
~~~

The 26 database checks retain branching, isolation, cancellation, persistence,
retry and feedback coverage. The inherited desktop/mobile browser suite checks
composer and message actions. The response suite covers:

- Short/natural and long/capped layout; delayed hover and early cancellation.
- Inner/outer wheel routing, focus/Escape, and simulated direct touch.
- Nested vertical scrolling together with horizontal code scrolling.
- Python/JavaScript/CSS highlighting, multiple blocks, unfinished fences,
  unknown-language and missing-library fallbacks.
- Exact-source copying, temporary feedback and clipboard denial.
- Sanitized Markdown and sanitized highlighter output.
- No highlighting per token, plus reuse of final highlight results.
- Container identity, focus and reading positions across a real HTTP stream
  and final database reconciliation.
- Main scrolling during generation, the new-content indicator and latest button.
- Desktop and 390px-wide layouts, plus dark and light code palettes.

Tests use isolated SQLite databases and Edge profiles under data/ and controlled
Ollama responses. They do not modify saved chats or require a live model. Touch
checks simulate pointer events; physical-device momentum and trackpad gestures
should also be checked manually.

For a manual check, ask for a long explanation containing several code blocks,
a wide table and long code lines. During generation, scroll up in the chat.
Then hover briefly and for at least 850ms, try keyboard scrolling and Escape,
copy each block, refresh the page, and switch branches.

Verified on October 6, 2026: all 26 backend tests, inherited desktop/mobile
browser checks, dedicated response UI checks on both layouts, and actual
restart checks for all 11 runtime files passed.
