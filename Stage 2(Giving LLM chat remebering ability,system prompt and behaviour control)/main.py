from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel
import ollama

app = FastAPI()

class Message(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: list[Message]

@app.post("/chat")
def chat(request: ChatRequest):

    messages = [
        {
    "role": "system",
    "content": """
You are a helpful conversational AI assistant.

Carefully use the entire conversation history when responding.

When the user provides information earlier in the conversation,
remember and use that information when it becomes relevant later.

Keep information about the user separate from information about yourself.

Understand follow-up questions in the context of previous messages.

Give clear, conversational, and useful answers.
"""
}
    ]

    messages.extend([
        {
            "role": message.role,
            "content": message.content
        }
        for message in request.messages
    ])

    print("\nMESSAGES SENT TO MODEL:")
    print(messages)

    response = ollama.chat(
        model="llama3.2:3b",
        messages=messages
    )

    return {
        "reply": response["message"]["content"]
    }

@app.get("/")
def home():
    return FileResponse(Path(__file__).with_name("index.html"))