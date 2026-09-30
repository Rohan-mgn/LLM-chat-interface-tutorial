from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel
import ollama

app = FastAPI()

class ChatRequest(BaseModel):
    message: str

@app.post("/chat")
def chat(request: ChatRequest):

    response = ollama.chat(
        model="llama3.2:3b",
        messages=[
            {
                "role": "user",
                "content": request.message
            }
        ]
    )

    return {
        "reply": response["message"]["content"]
    }


@app.get("/")
def home():
    return FileResponse(Path(__file__).with_name("index.html"))
