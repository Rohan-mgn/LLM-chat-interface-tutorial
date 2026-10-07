# Stage 9 — Files, attachments and RAG

This stage copies Stage 8 and adds Lessons 17–18. Earlier stages and their databases are unchanged.

## Run

From the project root in PowerShell:

~~~powershell
.\.venv\Scripts\python.exe -m pip install -r "Stage 9(Files attachments and RAG)/requirements-dev.txt"
ollama pull llama3.2:3b
ollama pull embeddinggemma
.\.venv\Scripts\python.exe "Stage 9(Files attachments and RAG)/run.py"
~~~

The models are already installed on the development machine. Pull commands are only needed if a model is missing. Keep Ollama running; open http://127.0.0.1:8000. If another stage uses that port, stop it or add --port 8009.

run.py restarts the server when an application Python, HTML, CSS, JavaScript or vendor file changes. It does not watch uploaded documents, SQLite writes, tests or documentation. Refresh the browser after frontend changes.

All persistent locations are derived from main.py, not the terminal's working directory:

~~~text
<project root>/data/stage9/
  chat.db
  uploads/
    <generated ID>.<extension>
    .tmp/                       multipart upload spooling
~~~

With this checkout, that is D:\Projects\LLM-chat-interface-tutorial\data\stage9. The startup log prints the resolved database and upload paths. A separate Stage 9 database avoids modifying Stage 8's saved chats. data/ is ignored by Git.

## Try it

1. Click **Attach files**, drop files onto the composer, or paste supported files/images into the textarea.
2. The composer shows upload progress, success or a retryable failure. Each message allows four files, totaling 20 MiB, with a 10 MiB individual limit.
3. Open **Files in this chat** to see extraction/indexing status. Text documents uploaded from the composer are selected automatically and enable **Use files to answer**. Sending waits for selected sources to become ready. Turn the toggle off to chat normally.
4. Ask a document question. The status shows **Searching your files...** before the streamed answer.
5. Click a citation or a source button to inspect the exact retrieved passage and its page, section or CSV row. Download retrieves the uploaded file.
6. On refresh, SQLite restores messages, attachments and citations. Select the desired ready documents and enable Use files again; that toggle resets on a full reload so document use remains explicit.
7. New chat keeps existing conversations and starts with no selected files.

Remove on a composer chip detaches that file from the upcoming message and source selection. The uploaded document remains in this chat's Files panel, where it can be deleted. This avoids silently destroying an upload that may be used by another message.

## What RAG does

Uploading stores bytes; it does not teach the model those bytes. RAG extracts readable text, splits it into passages, embeds those passages, retrieves a few relevant passages for each question, and supplies them as evidence to the chat model.

| Concept | What is stored or sent |
| --- | --- |
| Conversation history | User/assistant turns from the selected message branch |
| Attachment | File bytes, metadata and its message associations |
| RAG index | Extracted passages and numerical vectors persisted in SQLite |
| Retrieval | Only relevant passages from explicitly selected files in the current conversation |
| Fine-tuning/training | Not performed; model weights never change |

~~~mermaid
flowchart LR
  A[Upload] --> B[Validate and store]
  B --> C[Extract and normalize]
  C --> D[Chunk]
  D --> E[EmbeddingGemma]
  E --> F[SQLite chunks and vectors]
  Q[User question + recent selected history] --> R[Standalone search query]
  R --> S[Scoped cosine retrieval]
  F --> S
  S --> T[Untrusted excerpts + original question]
  T --> U[llama3.2 streaming answer]
  U --> V[Validated citations and saved response]
~~~

## Lesson 17 implementation

- Native multi-file picker, drag/drop, clipboard files, filename/type/size, removable chips, upload progress and retry.
- FastAPI UploadFile and multipart parsing; bounded request bodies and server-side size/count/total limits.
- TXT, Markdown and CSV require valid UTF-8/UTF-16 text; PDFs require a PDF signature and successful parsing; DOCX is checked as an Office ZIP/XML document.
- PNG/JPEG/WebP are decoded, dimension-limited and re-encoded before serving. Filenames are sanitized for display; storage uses generated IDs. No user filename is treated as a trusted path.
- files stores conversation ID, name, MIME type, size, fingerprint, state and timestamps. message_files associates several files with a user message, including edited branches. Both have foreign keys; an additional trigger rejects cross-conversation associations.
- Historical attachments, image thumbnails, safe downloads, document previews, unavailable-file errors and accessible controls.
- Deleting a conversation cascades to its files and chunks. A transactional pending_unlinks outbox removes physical files and retries failed removals on startup. Unreferenced physical files older than a day are cleaned up.
- Deleting a message association does not destroy a shared conversation document. This stage has no individual-message deletion action.
- All files stay in this project on D:. Upload content is never mounted as static executable web content. Downloads use nosniff and attachment disposition; only validated images can be served inline.

Images are attachment previews/downloads, not vision-model or OCR inputs. Scanned PDFs without text fail with an OCR explanation. DOCX supports paragraph/table-cell text but does not preserve full Word layout.

## Lesson 18 implementation

### Extraction, chunking and persistence

documents.py extracts text in bounded sections and normalizes Unicode/newlines. PDFs preserve real page numbers; Markdown preserves heading labels; CSV repeats column names for every complete row and retains row numbers. DOCX retains paragraph numbers.

rag.py uses paragraph/newline boundaries where available, 1,400-character chunks and 180-character overlap. CSV rows stay intact. Defaults are constants near the top of the module; changing chunk/parser settings changes the index compatibility fingerprint.

EmbeddingGemma is separate from the chat model. Documents use its title/text prefix, queries use its search-query prefix. Embeddings are batched in groups of 12, normalized and stored as JSON vectors in SQLite. This intentionally uses a bounded local cosine scan, not a new vector database.

SHA-256 detects identical file content within a conversation. Re-uploading identical content reuses the file and index. Re-index skips unchanged content with a matching configuration. Model name, model digest, vector dimension, extraction/chunk versions, prompt conventions and cosine metric are checked before retrieval. Changed/incompatible settings require Re-index; incompatible vectors are never silently mixed.

Jobs expose uploaded, extracting, chunking, embedding, ready and failed states. One indexing job runs at a time; duplicate jobs for a file are ignored. Failed/interrupted jobs can be retried. Startup marks interrupted jobs as failed rather than embedding everything again.

### Retrieval and follow-up questions

Use files sends explicit file IDs with the latest user message. Both attachment binding and retrieval validate conversation ownership. File lists are also a precise way to limit retrieval by document type.

For follow-up questions, a separate bounded model call rewrites the search query using only recent selected-branch history. The answer still receives the original question. A failed rewrite falls back to the original question, with that fallback recorded in diagnostics.

Retrieval uses a cosine threshold of 0.35, up to five chunks, exact/near-duplicate suppression and an 8,500-character passage budget. If nothing meets the threshold, a deterministic no-evidence reply is streamed without asking the model to guess. Scores are similarity values, not probabilities.

History, retrieved context and output each have bounds. The final prompt is capped conservatively at 14,000 UTF-8 bytes plus framing/output reservations for a 16,384-token model context. Older turns are dropped first. If the current question and evidence still do not fit, the UI explains the budget failure. Diagnostics provide an approximate token count, not a tokenizer-exact count.

### Boundaries and citations

The system prompt is preserved and extended with evidence-grounding rules. Documents are explicitly lower-priority data. Their text cannot create tools, change the system role, or authorize actions.

The model uses short labels such as [S1]. The server maps those labels to stable chunk IDs derived from file ID, chunk position and text. Unknown source IDs are removed even when split across streamed chunks. Only actually cited, actually retrieved passages become persisted citations. The UI inserts source text using textContent; Markdown still passes through Marked and DOMPurify.

Clicking a citation opens the passage from the current conversation's source endpoint. Unchanged text retains its source ID across re-embedding. Replacing/removing a document invalidates obsolete sources; earlier generated answer text remains as historical conversation text.

Prompt boundaries reduce injection risk; they cannot prove model obedience or factual correctness. No-citation answers show a verification notice. A valid source ID proves the source was retrieved, not that the generated claim logically follows from it.

### Updating files

**Replace** uploads a new version, validates it, moves message associations/source selections to it, removes the old vectors/citations and stored file, then indexes the replacement. Identical content is reused. **Re-index** retries failure or rebuilds after a parser/embedding configuration change. Do not edit generated upload files directly.

CSV support is row-based retrieval, not an analytics engine. A few retrieved rows cannot justify full-dataset totals. The prompt makes that limitation explicit; SQL/dataframe tools can be added in a later lesson.

### Diagnostics and limits

Set RAG_DEBUG=1 before starting to expose scoped retrieval diagnostics:

~~~powershell
$env:RAG_DEBUG = "1"
.\.venv\Scripts\python.exe "Stage 9(Files attachments and RAG)/run.py"
~~~

The assistant's **Retrieval details** button shows original/re-written queries, source IDs/scores, context, timing and approximate usage. The endpoint is disabled by default. Remove the environment variable to disable it:

~~~powershell
Remove-Item Env:RAG_DEBUG
~~~

| Bound | Default |
| --- | --- |
| Per file / per message | 10 MiB / four files and 20 MiB |
| Per conversation | 40 files, 50 MiB stored files, 4,000 chunks |
| Per document | 500,000 extracted characters, 600 chunks |
| PDF / images | 200 pages / 20 megapixels |
| DOCX expanded size | 20 MiB |
| Model output | 2,048 tokens |
| Embedding job concurrency | One |

This is a local, single-user tutorial app bound to 127.0.0.1, with one worker. Conversation IDs enforce data scoping but are not user authentication. Do not expose it publicly without authentication/authorization, hardened parser isolation and deployment controls. Production-grade OCR, vision, distributed jobs, FTS/vector acceleration, rerankers, exact tabular analytics and project/user permissions remain later extensions.

## API additions

| Method and path | Purpose |
| --- | --- |
| GET /files/config | Limits and diagnostic availability |
| GET /conversations/{id}/files | Scoped document metadata/status |
| POST /conversations/{id}/files | Upload one multipart field named upload |
| PUT /conversations/{id}/files/{file_id} | Replace a document |
| DELETE /conversations/{id}/files/{file_id} | Remove bytes, vectors and source relationships |
| POST /conversations/{id}/files/{file_id}/reindex | Retry/rebuild index |
| GET /conversations/{id}/files/{file_id}/download | Safe download; preview=true only inlines images |
| GET /conversations/{id}/files/{file_id}/preview | Bounded extracted-text preview |
| GET /conversations/{id}/sources/{source_id} | Exact passage and provenance |
| GET /conversations/{id}/messages/{message_id}/retrieval | Opt-in diagnostics |

POST /chat retains Stage 8's generation ID/action/branch fields and adds attachment_ids, use_files and file_ids. The browser still sends only the newest user message. Edit/retry/regenerate reuse the original user turn's attachment/retrieval configuration. API routes precede the static mount.

## Tests and evaluation

Run from the project root:

~~~powershell
.\.venv\Scripts\python.exe -B "Stage 9(Files attachments and RAG)/tests/check_database.py"
.\.venv\Scripts\python.exe -B "Stage 9(Files attachments and RAG)/tests/check_rag.py"
.\.venv\Scripts\python.exe -B "Stage 9(Files attachments and RAG)/tests/check_browser.py"
.\.venv\Scripts\python.exe -B "Stage 9(Files attachments and RAG)/tests/check_browser.py" --files
.\.venv\Scripts\python.exe -B "Stage 9(Files attachments and RAG)/tests/check_browser.py" --responses
.\.venv\Scripts\python.exe -B "Stage 9(Files attachments and RAG)/tests/check_reload.py"
~~~

These use isolated databases/browser profiles under project data/. The ordinary regression tests use deterministic model doubles, so they do not require Ollama. Edge is used for real desktop/mobile DOM, event, upload and streaming tests.

With Ollama running, execute the live evaluation:

~~~powershell
.\.venv\Scripts\python.exe -B "Stage 9(Files attachments and RAG)/tests/check_live_rag.py"
~~~

It indexes Markdown, text and CSV, ranks known sources, checks a cited Blue Falcon answer and a follow-up, verifies the secret code 8472 is absent in a different conversation, and reopens the database in a fresh application lifespan. It never modifies saved user chats.

The live fixture reports Hit@K, precision over returned hits, expected-file recall, reciprocal rank/MRR, retrieval latency, total answer latency and valid-citation-ID precision. These source-level metrics are deliberately distinguished from human evaluation:

- Correctness: does the answer state the expected fact without adding conflicting facts?
- Groundedness: is each document claim supported by an excerpt?
- Relevance: does it answer the original question rather than the retrieval rewrite?
- Citation precision: does each cited passage support the claim it accompanies?
- Citation coverage: what fraction of externally checkable document claims have supporting citations?

Score those five dimensions against the printed answers and source passages; a keyword match or valid source ID is not a substitute for that review. Expand the fixtures with empty/no-answer documents, paraphrases, conflicting versions, unusual PDF layouts and your actual use cases before changing thresholds.

The development live run retrieved the expected source first for all three fixture queries (Hit@K=1 and MRR=1 on this small fixture). This is a smoke-test result, not a general retrieval-quality guarantee. The deterministic suite separately checks rejection, deletion/replacement, indexing failure/recovery, model incompatibility, split citation filtering, context budgets, branch scope, CSV rows and PDF page provenance.

## References

- [Ollama embedding API](https://docs.ollama.com/api/embed): batched inputs and truncate=false.
- [EmbeddingGemma model card](https://ai.google.dev/gemma/docs/embeddinggemma/model_card): retrieval query/document prefixes and model limits.
- [pypdf text extraction](https://pypdf.readthedocs.io/en/stable/user/extract-text.html): text extraction, page complexity and OCR limitations.
