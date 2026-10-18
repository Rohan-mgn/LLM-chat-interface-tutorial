"""Offline output-release races using real SQLite and ASGI sends."""
import asyncio
from contextlib import closing
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/"tests"))
import check_rag as fixtures
from stream_fixture import start_stream, until
from RAG.evidence_release import OutputQueue, QUEUE_SIZE, DELTA_CHARS, release_gate, EvidenceRevision


class ReleaseTests(unittest.TestCase):
    setUp=fixtures.Checks.setUp
    tearDown=fixtures.Checks.tearDown
    upload=fixtures.Checks.upload
    rows=fixtures.Checks.rows
    send=fixtures.Checks.send

    async def request(self,file=None,use_files=True):
        return await self.app.chat(self.app.ChatRequest(conversation_id=self.cid,generation_id=uuid4(),
            message="What is the project codename?",use_files=use_files and file is not None,
            file_ids=[file["id"]] if file and use_files else []))

    async def queued(self,response):
        for _ in range(500):
            if any(e["type"]=="delta" for e in response.job["queue"]._queue):
                return
            if response.job["task"].done():
                self.fail("Producer finished before the expected queued delta")
            await asyncio.sleep(.01)
        self.fail("No delta queued")

    def test_queue_bounded_and_progress_coalesced(self):
        queue=OutputQueue({})
        for i in range(100):
            queue.put_nowait({"type":"status","message":"progress"*300})
        self.assertEqual(queue.qsize(),QUEUE_SIZE)
        self.assertTrue(all(len(e["message"])<=1024 for e in queue._queue))

    def test_queued_answer_deleted_before_release_is_never_persisted(self):
        file=self.upload()
        async def run():
            response=await self.request(file)
            await self.queued(response)
            self.assertEqual(self.rows()[-1]["content"],"")
            await asyncio.to_thread(self.app.documents.delete,self.cid,file["id"])
            transport,events=start_stream(response)
            await asyncio.wait_for(transport,5)
            packets=list(events._queue)
            self.assertFalse(any(p["type"]=="delta" for p in packets))
            self.assertEqual(packets[-1]["status"],"stopped")
            self.assertTrue(any("source changed" in p.get("message","") for p in packets))
        asyncio.run(run())
        self.app.init_db()
        row=self.rows()[-1]
        self.assertEqual((row["content"],row["status"]),("","stopped"))
        with closing(self.app.get_connection()) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM rag_runs WHERE message_id=?",(row["id"],)).fetchone())

    def test_permission_change_after_queueing_blocks_release(self):
        file=self.upload()
        async def run():
            response=await self.request(file);await self.queued(response)
            with self.app.documents.mutation(self.cid),closing(self.app.get_connection()) as db,db:
                db.execute("UPDATE rag_turns SET file_ids='[]' WHERE message_id=?",(response.job["user_message_id"],))
            transport,events=start_stream(response)
            await asyncio.wait_for(transport,5)
            self.assertFalse(any(e["type"]=="delta" for e in events._queue))
        asyncio.run(run())
        self.assertEqual(self.rows()[-1]["status"],"stopped")

    def test_background_extraction_change_cancels_queued_answer(self):
        file=self.upload()
        async def run():
            response=await self.request(file);await self.queued(response)
            await asyncio.to_thread(self.app.documents.store_extracted,self.cid,file["id"],[{"text":"Replacement content."}])
            transport,events=start_stream(response)
            await asyncio.wait_for(transport,5)
            self.assertFalse(any(e["type"]=="delta" for e in events._queue))
        asyncio.run(run())
        self.assertEqual(self.rows()[-1]["status"],"stopped")

    def test_unchanged_extraction_keeps_revision_valid(self):
        file=self.upload()
        async def run():
            response=await self.request(file);await self.queued(response)
            before=response.job["evidence_revision"]
            await asyncio.to_thread(self.app.documents.store_extracted,self.cid,file["id"],json.loads(file["extracted"]))
            before.validate(self.app.documents)
            transport,events=start_stream(response)
            await asyncio.wait_for(transport,5)
            self.assertEqual(list(events._queue)[-1]["status"],"completed")
        asyncio.run(run())

    def test_use_files_false_has_no_source_dependency(self):
        file=self.upload()
        async def run():
            response=await self.request(file,use_files=False);await self.queued(response)
            self.assertNotIn("evidence_revision",response.job)
            await asyncio.to_thread(self.app.documents.delete,self.cid,file["id"])
            transport,events=start_stream(response)
            await asyncio.wait_for(transport,5)
            self.assertEqual(list(events._queue)[-1]["status"],"completed")
        asyncio.run(run())

    def test_disconnect_and_send_timeout_do_not_persist_unsent_text(self):
        for timeout in (False,True):
            async def run():
                response=await self.request()
                async def receive():await asyncio.Event().wait()
                async def send(event):
                    if event["type"]=="http.response.body" and b'"type": "delta"' in event.get("body",b""):
                        if timeout:await asyncio.sleep(30)
                        raise OSError("connection lost")
                with patch.object(self.app,"SEND_TIMEOUT",.05):
                    with self.assertRaises((OSError,TimeoutError,Exception)):
                        await response({"type":"http","asgi":{"spec_version":"2.4"}},receive,send)
                self.assertFalse(self.app.conversation_lock.locked())
            asyncio.run(run())
            self.assertEqual(self.rows()[-1]["content"],"")
            self.assertEqual(self.rows()[-1]["status"],"stopped")

    def test_replacement_during_draft_releases_no_unverified_prefix(self):
        file=self.upload()
        async def run():
            first=asyncio.Event();resume=asyncio.Event()
            original=fixtures.FakeModel.chat
            async def controlled(raw,**kwargs):
                if not kwargs.get("stream"):return await original(raw,**kwargs)
                async def chunks():
                    yield {"message":{"content":"Already sent. "}}
                    first.set()
                    await resume.wait()
                    yield {"message":{"content":"Must not be released."}}
                return chunks()
            with patch.object(fixtures.FakeModel,"chat",controlled):
                response=await self.request(file)
                transport,events=start_stream(response)
                await asyncio.wait_for(first.wait(),5)
                await asyncio.to_thread(self.app.documents.replace,self.cid,file["id"],"new.txt","text/plain",b"A new document.")
                resume.set()
                await asyncio.wait_for(transport,5)
                emitted="".join(e["content"] for e in events._queue if e["type"]=="delta")
                self.assertEqual(emitted,"")
        asyncio.run(run())
        self.assertEqual((self.rows()[-1]["content"],self.rows()[-1]["status"]),("","stopped"))
        self.assertIsNone(self.app.documents.row(self.cid,file["id"]))

    def test_revocation_during_model_wait_cancels_without_waiting_for_a_token(self):
        file=self.upload()
        async def run():
            entered=asyncio.Event()
            original=fixtures.FakeModel.chat
            async def controlled(raw,**kwargs):
                if not kwargs.get("stream"):return await original(raw,**kwargs)
                async def chunks():
                    entered.set()
                    await asyncio.Event().wait()
                    yield {"message":{"content":"Never"}}
                return chunks()
            with patch.object(fixtures.FakeModel,"chat",controlled):
                response=await self.request(file)
                transport,events=start_stream(response)
                await asyncio.wait_for(entered.wait(),5)
                await asyncio.to_thread(self.app.documents.delete,self.cid,file["id"])
                await asyncio.wait_for(transport,2)
                self.assertEqual(list(events._queue)[-1]["status"],"stopped")
            self.assertTrue(self.app.provider.text_slots.acquire(blocking=False))
            self.app.provider.text_slots.release()
        asyncio.run(run())

    def test_cross_process_commit_waits_for_release_then_blocks_later_output(self):
        file=self.upload()
        async def run():
            entered=asyncio.Event();finish_send=asyncio.Event();next_token=asyncio.Event()
            original_delta=self.app.queue_delta
            released=[]
            async def bounded_delta(docs,job,text):
                await original_delta(docs,job,text)
                if not released:
                    released.append(text)
                    await next_token.wait()
            async def sending(event):
                if event["type"]=="delta":
                    entered.set()
                    await finish_send.wait()
            with patch.object(self.app,"queue_delta",bounded_delta):
                response=await self.request(file)
                transport,events=start_stream(response,sending)
                await asyncio.wait_for(entered.wait(),5)
                worker=await asyncio.create_subprocess_exec(sys.executable,"-B",str(ROOT/"tests/release_worker.py"),
                    str(self.app.DATABASE),str(self.root),str(self.cid),file["id"],
                    stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
                try:
                    self.assertEqual((await asyncio.wait_for(worker.stdout.readline(),5)).strip(),b"ready")
                    await asyncio.sleep(.1)
                    self.assertIsNone(worker.returncode)
                    self.assertIsNotNone(self.app.documents.row(self.cid,file["id"]))
                    # Independent conversations do not share this release gate.
                    async with asyncio.timeout(.5):
                        async with release_gate(self.app.documents,self.cid+1000):pass
                    finish_send.set()
                    stdout,stderr=await asyncio.wait_for(worker.communicate(),5)
                    self.assertEqual(worker.returncode,0,stderr.decode())
                    self.assertIn(b"committed",stdout)
                    next_token.set()
                    await asyncio.wait_for(transport,5)
                    text="".join(e["content"] for e in events._queue if e["type"]=="delta")
                    self.assertEqual(text,"".join(released));self.assertTrue(text.startswith("Blue Falcon"))
                    self.assertEqual(list(events._queue)[-1]["status"],"stopped")
                finally:
                    if worker.returncode is None:
                        worker.kill();await worker.wait()
                    finish_send.set();next_token.set()
                    if not transport.done():
                        transport.cancel();await asyncio.gather(transport,return_exceptions=True)
        asyncio.run(run())
        self.assertTrue(self.rows()[-1]["content"].startswith("Blue Falcon"))

    def test_revocation_after_last_send_before_completion_never_marks_complete(self):
        file=self.upload()
        async def run():
            closing=asyncio.Event();resume=asyncio.Event()
            original=fixtures.FakeModel.chat
            answer_clients=set()
            async def tracked(raw,**kwargs):
                if kwargs.get("stream"):answer_clients.add(raw)
                return await original(raw,**kwargs)
            async def close(model):
                if model in answer_clients:
                    closing.set();await resume.wait()
            with patch.object(fixtures.FakeModel,"chat",tracked),patch.object(fixtures.FakeModel,"close",close):
                response=await self.request(file)
                transport,events=start_stream(response)
                await asyncio.wait_for(closing.wait(),5)
                await asyncio.to_thread(self.app.documents.delete,self.cid,file["id"])
                resume.set()
                await asyncio.wait_for(transport,5)
                self.assertEqual(list(events._queue)[-1]["status"],"stopped")
                self.assertEqual("".join(e["content"] for e in events._queue if e["type"]=="delta"),self.rows()[-1]["content"])
        asyncio.run(run())


if __name__=="__main__":
    unittest.main(verbosity=2)
