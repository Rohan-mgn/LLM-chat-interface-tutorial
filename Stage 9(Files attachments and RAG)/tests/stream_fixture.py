"""Exercise the ASGI send boundary, not just a response iterator."""
import asyncio
import json


def start_stream(response, on_event=None):
    events = asyncio.Queue()
    async def receive():
        await asyncio.Event().wait()
    async def send(event):
        if event["type"] == "http.response.body" and event.get("body"):
            for packet in event["body"].decode().strip().split("\n\n"):
                item=json.loads(packet[6:])
                if on_event is not None:
                    await on_event(item)
                events.put_nowait(item)
    task=asyncio.create_task(response({"type":"http","asgi":{"spec_version":"2.4"}},receive,send))
    return task,events


async def until(events, kind):
    while True:
        event=await asyncio.wait_for(events.get(),5)
        if event["type"]==kind:
            return event
