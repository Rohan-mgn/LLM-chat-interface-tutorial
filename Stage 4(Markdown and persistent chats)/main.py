from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
import ollama

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI()

# Serve the CSS and JavaScript used by index.html.
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

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

Use Markdown when it makes the answer easier to read:
- Use headings for longer answers and bold text for important points.
- Use ordered or unordered lists for steps and related items.
- Use Markdown tables when comparing information.
- Put code in fenced code blocks with the language name after the opening fence.
- Use inline code for short code snippets, commands, and filenames.
Keep short answers simple. Use blank lines between paragraphs and blocks.
Write Markdown, not raw HTML. Do not wrap the entire answer in a code block
unless the user specifically asks for code only.
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


    def generate():

        stream = ollama.chat(
            model="llama3.2:3b",
            messages=messages,
            stream=True
        )

        for chunk in stream:
            content = chunk["message"]["content"]
            if content:
                yield content


    return StreamingResponse(
        generate(),
        media_type="text/plain"
    )

@app.get("/")
def home():
    return FileResponse(BASE_DIR / "static" / "index.html")
