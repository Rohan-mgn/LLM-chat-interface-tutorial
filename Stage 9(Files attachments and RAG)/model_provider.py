"""Ollama adapter. No model downloads; resource slots are held per call/stream."""
import asyncio
import json
import os
from threading import BoundedSemaphore, Lock
from collections import deque
import ollama
from context_budget import check, CONTEXT_TOKENS, OUTPUT_TOKENS

CHAT_MODEL = os.getenv("CHAT_MODEL", "llama3.2:3b")
EMBED_MODEL = os.getenv("EMBED_MODEL", "embeddinggemma:latest")
VISION_MODEL = os.getenv("VISION_MODEL", "").strip()

class ProviderClient:
    def __init__(self, provider, timeout=120):
        self.provider = provider
        self.raw = ollama.AsyncClient(timeout=timeout)
        self.held = None

    async def _take(self, slot):
        # A threading semaphore also bounds legacy synchronous title calls and
        # works across TestClient/event-loop lifetimes. Waiting is cancellable.
        await self.provider.acquire(slot)
        self.held = slot

    def _release(self):
        if self.held is not None:
            self.held.release()
            self.held = None

    async def chat(self, **kwargs):
        options = dict(kwargs.get("options") or {})
        output = int(options.get("num_predict", OUTPUT_TOKENS))
        check(kwargs.get("messages", []), output)
        options.update(num_ctx=CONTEXT_TOKENS, num_predict=output)
        kwargs["options"] = options
        slot = self.provider.vision_slots if kwargs.get("model") == self.provider.vision_model and self.provider.vision_model else self.provider.text_slots
        await self._take(slot)
        try:
            response = await self.raw.chat(**kwargs)
            if not kwargs.get("stream"):
                self._release()
                return response
            async def stream():
                try:
                    async for part in response:
                        yield part
                finally:
                    await response.aclose()
                    self._release()
            return stream()
        except BaseException:
            self._release()
            raise

    async def embed(self, **kwargs):
        async with self.provider.slot(self.provider.embedding_slots):
            return await self.raw.embed(**kwargs)

    async def list(self):
        return await self.raw.list()

    async def show(self, model):
        return await self.raw.show(model)

    async def close(self):
        try:
            await self.raw.close()
        finally:
            self._release()

class ModelProvider:
    def __init__(self):
        self.chat_model = CHAT_MODEL
        self.embed_model = EMBED_MODEL
        self.vision_model = VISION_MODEL
        self.text_concurrency = max(1, int(os.getenv("TEXT_MODEL_CONCURRENCY", "1")))
        self.text_slots = BoundedSemaphore(self.text_concurrency)
        self.embedding_slots = BoundedSemaphore(1)
        self.vision_slots = BoundedSemaphore(1)
        self.waiter_guard=Lock()
        self.waiters={}

    def client(self, timeout=120):
        return ProviderClient(self, timeout)

    async def acquire(self,semaphore):
        ticket=object()
        with self.waiter_guard:
            queue=self.waiters.setdefault(semaphore,deque())
            queue.append(ticket)
        try:
            while True:
                with self.waiter_guard:
                    if queue[0] is ticket and semaphore.acquire(blocking=False):
                        queue.popleft()
                        return
                await asyncio.sleep(.01)
        finally:
            with self.waiter_guard:
                if ticket in queue:queue.remove(ticket)

    def slot(self, semaphore):
        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def acquired():
            await self.acquire(semaphore)
            try:
                yield
            finally:
                semaphore.release()
        return acquired()

    def chat_sync(self, **kwargs):
        options=dict(kwargs.get("options") or {})
        output=int(options.get("num_predict",OUTPUT_TOKENS))
        check(kwargs.get("messages",[]),output)
        options.update(num_ctx=CONTEXT_TOKENS,num_predict=output)
        kwargs["options"]=options
        # Titles are optional and must not wait behind a long generation forever.
        if not self.text_slots.acquire(timeout=12):
            raise ValueError("Title generation is busy.")
        client=None
        try:
            client=ollama.Client(timeout=12)
            return client.chat(**kwargs)
        finally:
            if client is not None and hasattr(client,"close"):client.close()
            self.text_slots.release()

    async def structured(self, messages, schema, *, timeout=20, output=768, model=None, images=None):
        client = self.client(timeout=timeout)
        try:
            if images:
                messages = [*messages[:-1], {**messages[-1], "images": images}]
            async with asyncio.timeout(timeout):
                response = await client.chat(model=model or self.chat_model, messages=messages,
                    format=schema, stream=False, options={"temperature": 0, "num_predict": output})
            result = json.loads(response["message"]["content"])
            if not isinstance(result, dict):
                raise ValueError("Model returned an invalid structured response.")
            return result
        finally:
            await client.close()

    async def describe_embedding(self):
        client = self.client(30)
        try:
            result = await client.list()
            for model in result["models"]:
                if model["model"] in (self.embed_model, self.embed_model + ":latest"):
                    return model["digest"]
            raise ValueError(f"Embedding model {self.embed_model} is not available in Ollama.")
        finally:
            await client.close()

    def document_input(self, title, text):
        prefix = os.getenv("EMBED_DOCUMENT_PREFIX")
        if prefix is not None:
            return prefix + text
        return f"title: {title} | text: {text}" if self.embed_model.startswith("embeddinggemma") else title + "\n" + text

    def query_input(self, text):
        prefix = os.getenv("EMBED_QUERY_PREFIX")
        if prefix is not None:
            return prefix + text
        return "task: search result | query: " + text if self.embed_model.startswith("embeddinggemma") else text

    async def vision_available(self):
        if not self.vision_model:
            return False, "Vision/OCR is not enabled. Configure VISION_MODEL to use a model you installed."
        client = self.client(15)
        try:
            async with asyncio.timeout(15):
                result = await client.show(self.vision_model)
            if "vision" not in (result.get("capabilities") or []):
                return False, "The configured model does not advertise vision capability."
            return True, ""
        except Exception:
            return False, "The configured vision model is unavailable. Text-document tools remain available."
        finally:
            await client.close()
