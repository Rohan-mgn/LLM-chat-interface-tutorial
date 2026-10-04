"""SQLite message trees. This module knows nothing about HTTP or Ollama."""
from contextlib import closing


class TreeError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def migrate(connection):
    columns = {row['name'] for row in connection.execute('PRAGMA table_info(messages)')}
    if 'parent_id' not in columns:
        connection.execute('ALTER TABLE messages ADD COLUMN parent_id INTEGER REFERENCES messages(id) ON DELETE CASCADE')
        # Older databases contain flat histories (including old copied chats).
        # Link those rows in place, once, without changing their IDs or text.
        previous = {}
        for row in connection.execute('SELECT id, conversation_id FROM messages ORDER BY id').fetchall():
            connection.execute('UPDATE messages SET parent_id = ? WHERE id = ?',
                               (previous.get(row['conversation_id']), row['id']))
            previous[row['conversation_id']] = row['id']
    connection.execute("UPDATE messages SET status = 'completed' WHERE status = 'complete'")
    connection.execute('CREATE INDEX IF NOT EXISTS idx_message_parent ON messages(conversation_id, parent_id, id)')
    # Stop can arrive before the corresponding POST /chat. Remember that UUID
    # so a delayed request cannot start generating after cancellation returned.
    connection.execute('''CREATE TABLE IF NOT EXISTS generation_cancellations (
        id TEXT PRIMARY KEY, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )''')
    connection.execute('''CREATE TABLE IF NOT EXISTS branch_selections (
        conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        parent_key INTEGER NOT NULL,
        child_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
        PRIMARY KEY (conversation_id, parent_key)
    )''')
    # A parent must already exist in this conversation. Immutable edges then
    # prevent both cycles and accidental movement of an existing branch.
    connection.execute('''CREATE TRIGGER IF NOT EXISTS message_parent_scope
        BEFORE INSERT ON messages WHEN NEW.parent_id IS NOT NULL
        BEGIN
          SELECT RAISE(ABORT, 'Parent must belong to the same conversation')
          WHERE NOT EXISTS (SELECT 1 FROM messages
              WHERE id = NEW.parent_id AND conversation_id = NEW.conversation_id);
        END''')
    connection.execute('''CREATE TRIGGER IF NOT EXISTS message_edges_immutable
        BEFORE UPDATE OF parent_id, conversation_id ON messages
        WHEN NEW.parent_id IS NOT OLD.parent_id OR NEW.conversation_id != OLD.conversation_id
        BEGIN SELECT RAISE(ABORT, 'Message tree edges are immutable'); END''')


def ancestors(messages, message_id):
    by_id = {message['id']: message for message in messages}
    path, seen = [], set()
    while message_id is not None:
        if message_id in seen or message_id not in by_id:
            raise TreeError(409, 'Invalid message ancestry.')
        seen.add(message_id)
        message = by_id[message_id]
        path.append(message)
        message_id = message['parent_id']
    return list(reversed(path))


def active_path(messages, selections):
    children = {}
    for message in messages:
        children.setdefault(message['parent_id'] or 0, []).append(message)
    path, parent = [], 0
    while children.get(parent):
        siblings = children[parent]
        selected = next((m for m in siblings if m['id'] == selections.get(str(parent))), siblings[0])
        path.append(selected)
        parent = selected['id']
    return path


class TreeStore:
    def __init__(self, connect):
        self.connect = connect

    @staticmethod
    def _read(connection, conversation_id):
        if not connection.execute('SELECT 1 FROM conversations WHERE id = ?', (conversation_id,)).fetchone():
            raise TreeError(404, 'Conversation not found.')
        messages = [dict(row) for row in connection.execute(
            'SELECT * FROM messages WHERE conversation_id = ? ORDER BY id', (conversation_id,))]
        selections = {str(row['parent_key']): row['child_id'] for row in connection.execute(
            'SELECT parent_key, child_id FROM branch_selections WHERE conversation_id = ?', (conversation_id,))}
        return messages, selections

    @staticmethod
    def _select(connection, conversation_id, messages, message_id):
        # Select the ancestry, but retain every choice BELOW sibling branches.
        # Returning to a sibling therefore restores its own later conversation.
        for message in ancestors(messages, message_id):
            connection.execute('''INSERT INTO branch_selections(conversation_id, parent_key, child_id)
                VALUES (?, ?, ?) ON CONFLICT(conversation_id, parent_key)
                DO UPDATE SET child_id = excluded.child_id''',
                (conversation_id, message['parent_id'] or 0, message['id']))

    def tree(self, conversation_id, select_id=None):
        with closing(self.connect()) as connection, connection:
            # Read nodes and choices from one snapshot, even if another request
            # commits a new sibling while this tree is being loaded.
            connection.execute('BEGIN IMMEDIATE' if select_id is not None else 'BEGIN')
            messages, selections = self._read(connection, conversation_id)
            if select_id is not None:
                if not any(m['id'] == select_id for m in messages):
                    raise TreeError(404, 'Message not found in this conversation.')
                self._select(connection, conversation_id, messages, select_id)
                messages, selections = self._read(connection, conversation_id)
            return {'conversation_id': conversation_id, 'messages': messages,
                    'active_children': selections,
                    'active_path_ids': [m['id'] for m in active_path(messages, selections)]}

    def append(self, conversation_id, role, content):
        """Small import/test helper; application generations use reserve()."""
        with closing(self.connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            messages, selections = self._read(connection, conversation_id)
            path = active_path(messages, selections)
            parent_id = path[-1]['id'] if path else None
            message_id = connection.execute('''INSERT INTO messages
                (conversation_id, parent_id, role, content, status, reply_to_message_id)
                VALUES (?, ?, ?, ?, 'completed', ?)''',
                (conversation_id, parent_id, role, content, parent_id if role == 'assistant' else None)).lastrowid
            messages, _ = self._read(connection, conversation_id)
            self._select(connection, conversation_id, messages, message_id)
            self._touch(connection, conversation_id)
            return message_id

    @staticmethod
    def _touch(connection, conversation_id):
        connection.execute("UPDATE conversations SET updated_at = STRFTIME('%Y-%m-%d %H:%M:%f', 'now') WHERE id = ?", (conversation_id,))

    def reserve(self, request):
        """Insert new nodes atomically; never rewrite an earlier turn or reply."""
        cid, generation_id = request.conversation_id, str(request.generation_id)
        with closing(self.connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            if connection.execute('SELECT 1 FROM generation_requests WHERE id = ?', (generation_id,)).fetchone():
                raise TreeError(409, 'This generation request was already accepted.')
            if connection.execute('SELECT 1 FROM generation_cancellations WHERE id = ?', (generation_id,)).fetchone():
                raise TreeError(409, 'This generation request was cancelled before it started.')
            messages, selections = self._read(connection, cid)
            by_id = {m['id']: m for m in messages}
            target = by_id.get(request.message_id)
            if request.action != 'send' and target is None:
                raise TreeError(404, 'Message not found in this conversation.')
            attachment_ids = [str(value) for value in request.attachment_ids]
            if request.action != 'send' and attachment_ids:
                raise TreeError(422, 'Existing branches keep their own attachments; new uploads belong to a new send.')
            if request.action in ('send', 'edit') and not request.message.strip() and not attachment_ids:
                raise TreeError(422, 'Message must not be blank.')

            if request.action in ('send', 'edit'):
                if request.action == 'edit':
                    if target['role'] != 'user':
                        raise TreeError(422, 'Only user messages can be edited.')
                    if request.message == target['content']:
                        raise TreeError(409, 'The message is unchanged; no branch was created.')
                    parent_id = target['parent_id']
                else:
                    path = active_path(messages, selections)
                    parent_id = request.parent_id if 'parent_id' in request.model_fields_set else (path[-1]['id'] if path else None)
                    if parent_id is not None:
                        parent = by_id.get(parent_id)
                        if parent is None:
                            raise TreeError(404, 'Parent not found in this conversation.')
                        if parent['role'] != 'assistant' or parent['status'] == 'generating':
                            raise TreeError(409, 'Continue after an assistant response, or retry the unfinished user turn.')
                user_id = connection.execute('''INSERT INTO messages
                    (conversation_id, parent_id, role, content, status)
                    VALUES (?, ?, 'user', ?, 'completed')''', (cid, parent_id, request.message)).lastrowid
                from attachments import bind_files
                if request.action == 'edit':
                    attachment_ids = [row[0] for row in connection.execute('SELECT file_id FROM message_files WHERE message_id = ?', (target['id'],))]
                bind_files(connection, cid, user_id, attachment_ids, target['id'] if request.action == 'edit' else None)
                connection.execute("UPDATE conversations SET title = ? WHERE id = ? AND title_source = 'pending' AND title = 'New chat'",
                                   (' '.join(request.message.split())[:60] or 'Attachments', cid))
                if not request.message.strip():
                    connection.execute("UPDATE conversations SET title_source = 'fallback' WHERE id = ? AND title_source = 'pending'", (cid,))
            else:
                if target['role'] == 'assistant':
                    target = by_id.get(target['parent_id'])
                if target is None or target['role'] != 'user':
                    raise TreeError(422, 'This response has no parent user message.')
                user_id = target['id']

            # Regeneration and retry add an assistant sibling under the exact
            # same user node. Editing adds a user sibling under its old parent.
            assistant_id = connection.execute('''INSERT INTO messages
                (conversation_id, parent_id, role, content, status, reply_to_message_id, generation_id)
                VALUES (?, ?, 'assistant', '', 'generating', ?, ?)''',
                (cid, user_id, user_id, generation_id)).lastrowid
            connection.execute('INSERT INTO generation_requests VALUES (?, ?)', (generation_id, assistant_id))
            messages, _ = self._read(connection, cid)
            self._select(connection, cid, messages, assistant_id)
            self._touch(connection, cid)
            # Follow parent pointers, never ID order or all rows in the chat.
            # Incomplete assistant text remains visible but is not model context.
            context = [{'role': m['role'], 'content': m['content']}
                       for m in ancestors(messages, user_id) if m['status'] == 'completed' and m['content'].strip()]
            return {'conversation_id': cid, 'user_message_id': user_id,
                    'assistant_message_id': assistant_id, 'generation_id': generation_id,
                    'attachment_only': not next(m for m in messages if m['id'] == user_id)['content'].strip()}, context
