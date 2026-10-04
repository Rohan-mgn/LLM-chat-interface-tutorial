"""Attachment API/storage regression tests; isolated DBs, no real Ollama calls."""
import asyncio
from contextlib import closing
from io import BytesIO
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

from PIL import Image
import check_database as regression
import attachments
from upload_routes import MAX_REQUEST_SIZE


class AttachmentChecks(unittest.TestCase):
    setUp, tearDown = regression.Checks.setUp, regression.Checks.tearDown
    create, messages, events = regression.Checks.create, regression.Checks.messages, regression.Checks.events

    def upload(self, cid, name='notes.txt', data=b'private file contents', mime='text/plain', fid=None):
        return self.client.post(f'/conversations/{cid}/files/{fid or uuid4()}', files={'file': (name, data, mime)})

    def send(self, cid, ids=(), message='Store these files', **extra):
        return self.client.post('/chat', json={'conversation_id': cid, 'generation_id': str(uuid4()),
            'message': message, 'attachment_ids': list(ids), **extra})

    def disk(self):
        return list((self.root / 'uploads').iterdir())

    def image_bytes(self, format='PNG'):
        stream = BytesIO()
        Image.new('RGB', (16, 12), 'blue').save(stream, format=format)
        return stream.getvalue()

    def test_upload_bind_reload_and_model_never_receives_file_bytes(self):
        cid = self.create()
        response = self.upload(cid)
        self.assertEqual(response.status_code, 201, response.text)
        file = response.json()
        self.assertNotIn('storage_key', file)
        self.assertTrue(file['available'])
        self.events(self.send(cid, [file['id']]))
        self.assertEqual(self.messages(cid)[0]['attachments'], [file])
        self.assertNotIn('private file contents', str(self.calls))
        self.assertNotIn(file['original_filename'], str(self.calls))
        self.assertEqual(self.client.get(f'/conversations/{cid}/files').json()['files'], [])
        self.app.init_db()
        self.assertEqual(self.messages(cid)[0]['attachments'], [file])

    def test_attachment_only_send_is_local_acknowledgement(self):
        cid = self.create()
        file = self.upload(cid).json()
        events = self.events(self.send(cid, [file['id']], message=''))
        self.assertEqual(events[-1]['status'], 'completed')
        self.assertEqual(self.calls, [])
        self.assertIn('cannot read', self.messages(cid)[1]['content'])
        response = self.messages(cid)[1]
        self.events(self.send(cid, action='regenerate', message_id=response['id']))
        self.assertEqual(self.calls, [])
        self.assertIn('cannot read', self.messages(cid)[1]['content'])

    def test_missing_pending_file_cannot_be_sent(self):
        cid = self.create()
        file = self.upload(cid).json()
        row = self.app.files.find(cid, file['id'])
        self.app.files.path(row['storage_key']).unlink()
        self.assertEqual(self.send(cid, [file['id']]).status_code, 404)
        self.assertEqual(self.messages(cid), [])

    def test_multiple_files_formats_and_safe_image_previews(self):
        cid = self.create()
        samples = [('notes.md', b'# Notes', 'text/markdown'), ('data.csv', b'a,b\n1,2', 'text/csv'),
                   ('sample.pdf', b'%PDF-1.7\nopaque content\n%%EOF', 'application/pdf'),
                   ('image.png', self.image_bytes(), 'image/png'), ('photo.webp', self.image_bytes('WEBP'), 'image/webp')]
        files = []
        for name, data, mime in samples:
            response = self.upload(cid, name, data, mime)
            self.assertEqual(response.status_code, 201, response.text)
            files.append(response.json())
        self.events(self.send(cid, [file['id'] for file in files]))
        self.assertEqual(len(self.messages(cid)[0]['attachments']), 5)
        image = files[-1]
        response = self.client.get(f"/conversations/{cid}/files/{image['id']}?preview=true")
        self.assertEqual(response.headers['content-type'], 'image/png')
        with Image.open(BytesIO(response.content)) as preview:
            self.assertLessEqual(max(preview.size), 512)

    def test_jpeg_and_csv_windows_mime(self):
        cid = self.create()
        self.assertEqual(self.upload(cid, 'photo.jpg', self.image_bytes('JPEG'), 'image/jpeg').status_code, 201)
        self.assertEqual(self.upload(cid, 'data.csv', b'a,b', 'application/vnd.ms-excel').status_code, 201)

    def test_download_headers_and_filename_sanitization(self):
        cid = self.create()
        response = self.upload(cid, '../../outside.txt')
        self.assertEqual(response.status_code, 201, response.text)
        file = response.json()
        self.assertEqual(file['original_filename'], 'outside.txt')
        self.assertFalse((self.root / 'outside.txt').exists())
        response = self.client.get(f"/conversations/{cid}/files/{file['id']}")
        self.assertEqual(response.content, b'private file contents')
        self.assertIn('attachment;', response.headers['content-disposition'])
        self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
        self.assertIn('sandbox', response.headers['content-security-policy'])
        self.assertNotIn('\n', attachments.safe_name('bad\n\u202efilename.txt'))

    def test_cross_conversation_upload_binding_download_delete_are_scoped(self):
        a, b = self.create(), self.create()
        file = self.upload(a).json()
        self.assertEqual(self.send(b, [file['id']]).status_code, 404)
        self.assertEqual(self.messages(b), [])
        self.assertEqual(self.client.get(f"/conversations/{b}/files/{file['id']}").status_code, 404)
        self.assertEqual(self.client.delete(f"/conversations/{b}/files/{file['id']}").status_code, 404)
        self.assertEqual(self.upload(b, fid=file['id']).status_code, 404)
        self.assertTrue(self.app.files.public(self.app.files.find(a, file['id']))['available'])

    def test_reject_unsupported_spoofed_empty_binary_and_oversized(self):
        cid = self.create()
        cases = [('run.exe', b'MZ123', 'application/octet-stream', 415),
                 ('report.docx', b'PK123', 'application/octet-stream', 415),
                 ('fake.png', b'not an image', 'image/png', 415),
                 ('fake.jpg', self.image_bytes(), 'image/jpeg', 415),
                 ('fake.pdf', b'text', 'application/pdf', 415),
                 ('notes.txt', b'hello', 'image/png', 415),
                 ('notes.txt', b'\xff\x00', 'text/plain', 415),
                 ('notes.txt', b'', 'text/plain', 422),
                 ('large.txt', b'x' * (attachments.MAX_FILE_SIZE + 1), 'text/plain', 413)]
        for name, data, mime, code in cases:
            with self.subTest(name=name, mime=mime, size=len(data)):
                response = self.upload(cid, name, data, mime)
                self.assertEqual(response.status_code, code, response.text)
                self.assertEqual(self.disk(), [])

    def test_bounded_envelope_counts_chunked_actual_bytes(self):
        cid = self.create()
        response = self.client.post(f'/conversations/{cid}/files/{uuid4()}',
            content=iter([b'x' * (MAX_REQUEST_SIZE // 2), b'x' * (MAX_REQUEST_SIZE // 2 + 1)]),
            headers={'content-type': 'multipart/form-data; boundary=test'})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.disk(), [])

    def test_count_total_and_duplicate_binding_limits(self):
        cid = self.create()
        files = [self.upload(cid, data=b'x').json() for _ in range(5)]
        self.assertEqual(self.upload(cid, data=b'x').status_code, 413)
        self.assertEqual(self.send(cid, [files[0]['id']] * 2).status_code, 422)
        self.assertEqual(self.send(cid, [file['id'] for file in files] + [str(uuid4())]).status_code, 422)
        other = self.create()
        with patch.object(attachments, 'MAX_TOTAL_SIZE', 5):
            self.assertEqual(self.upload(other, data=b'123').status_code, 201)
            self.assertEqual(self.upload(other, data=b'456').status_code, 413)
        self.assertEqual(len(self.disk()), 6)

    def test_image_pixel_limit(self):
        cid = self.create()
        with patch.object(attachments, 'MAX_PIXELS', 10):
            self.assertEqual(self.upload(cid, 'large.png', self.image_bytes(), 'image/png').status_code, 415)
        self.assertEqual(self.disk(), [])

    def test_retry_idempotence_and_removed_upload_cannot_commit_late(self):
        cid, fid = self.create(), str(uuid4())
        first = self.upload(cid, fid=fid).json()
        self.assertEqual(self.upload(cid, fid=fid).json(), first)
        self.assertEqual(len(self.disk()), 1)
        self.assertEqual(self.client.delete(f'/conversations/{cid}/files/{fid}').status_code, 200)
        self.assertEqual(self.upload(cid, fid=fid).status_code, 409)
        late = str(uuid4())
        self.assertEqual(self.client.delete(f'/conversations/{cid}/files/{late}').status_code, 200)
        self.assertEqual(self.upload(cid, fid=late).status_code, 409)
        self.assertEqual(self.disk(), [])

    def test_edit_shares_files_and_regeneration_keeps_exact_user_association(self):
        cid = self.create()
        file = self.upload(cid).json()
        self.events(self.send(cid, [file['id']]))
        original = self.messages(cid)
        self.events(self.send(cid, action='edit', message_id=original[0]['id'], message='Edited'))
        edited = self.messages(cid)
        self.assertEqual(edited[0]['attachments'], original[0]['attachments'])
        self.events(self.send(cid, action='regenerate', message_id=edited[1]['id']))
        self.assertEqual(self.messages(cid)[0]['id'], edited[0]['id'])
        self.assertEqual(len(self.disk()), 1)
        self.assertEqual(self.client.delete(f"/conversations/{cid}/files/{file['id']}").status_code, 409)
        # Deleting one branch must retain a blob still used by another branch.
        with closing(self.app.get_connection()) as connection, connection:
            connection.execute('DELETE FROM messages WHERE id = ?', (original[0]['id'],))
        self.app.files.cleanup()
        self.assertEqual(len(self.disk()), 1)
        self.assertEqual(self.client.delete(f'/conversations/{cid}').status_code, 200)
        self.assertEqual(self.disk(), [])
        with closing(self.app.get_connection()) as connection:
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_sent_file_cannot_be_stolen_by_a_new_user_message(self):
        cid = self.create()
        file = self.upload(cid).json()
        self.events(self.send(cid, [file['id']]))
        self.assertEqual(self.send(cid, [file['id']], message='Reuse elsewhere').status_code, 409)
        self.assertEqual(len(self.messages(cid)), 2)

    def test_missing_files_are_reported_without_breaking_history(self):
        cid = self.create()
        file = self.upload(cid).json()
        self.events(self.send(cid, [file['id']]))
        row = self.app.files.find(cid, file['id'])
        self.app.files.path(row['storage_key']).unlink()
        self.assertFalse(self.messages(cid)[0]['attachments'][0]['available'])
        self.assertEqual(self.client.get(f"/conversations/{cid}/files/{file['id']}").status_code, 404)

    def test_expired_drafts_crash_orphans_and_failed_deletions_are_cleaned(self):
        cid = self.create()
        file = self.upload(cid).json()
        with closing(self.app.get_connection()) as connection, connection:
            connection.execute("UPDATE files SET created_at = datetime('now', '-2 days')")
        self.app.files.cleanup()
        self.assertEqual(self.disk(), [])
        orphan = self.root / 'uploads' / (uuid4().hex + '.blob')
        orphan.write_bytes(b'orphan')
        partial = self.root / 'uploads' / (uuid4().hex + '.' + uuid4().hex + '.part')
        partial.write_bytes(b'partial')
        self.app.files.cleanup(startup=True)
        self.assertEqual(self.disk(), [])
        file = self.upload(cid).json()
        row = self.app.files.find(cid, file['id'])
        with patch.object(Path, 'unlink', side_effect=PermissionError('Download still open')):
            self.assertEqual(self.client.delete(f"/conversations/{cid}/files/{file['id']}").status_code, 200)
        self.assertTrue(self.app.files.path(row['storage_key']).is_file())
        self.app.files.cleanup()
        self.assertEqual(self.disk(), [])

    def test_interrupted_write_and_database_failure_leave_no_blobs(self):
        cid = self.create()
        class Interrupted:
            filename, content_type = 'partial.txt', 'text/plain'
            reads = 0
            async def read(self, size):
                self.reads += 1
                if self.reads == 1:
                    return b'partial bytes'
                raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(self.app.files.upload(cid, str(uuid4()), Interrupted()))
        self.assertEqual(self.disk(), [])
        with closing(self.app.get_connection()) as connection, connection:
            connection.execute("CREATE TRIGGER fail_files BEFORE INSERT ON files BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        with self.assertRaises(Exception):
            self.upload(cid)
        self.assertEqual(self.disk(), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
