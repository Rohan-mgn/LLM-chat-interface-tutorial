"""Conservative model-independent token estimates; all provider calls use this budget."""
import math
import os

CONTEXT_TOKENS = max(4096, int(os.getenv("MODEL_CONTEXT_TOKENS", "16384")))
OUTPUT_TOKENS = 2048

def estimate(text):
    # ASCII-heavy prose is deliberately estimated at two characters/token.
    # Non-ASCII uses its UTF-8 byte count as a conservative bound.
    ascii_count = sum(ord(c) < 128 for c in text)
    return math.ceil(ascii_count / 2) + sum(len(c.encode("utf-8")) for c in text if ord(c) >= 128)

def message_tokens(messages):
    return 32 + sum(16 + estimate(m.get("content", "")) + 4096 * len(m.get("images") or []) for m in messages)

def input_limit(output=OUTPUT_TOKENS):
    return CONTEXT_TOKENS - output - max(512, CONTEXT_TOKENS // 10)

def check(messages, output=OUTPUT_TOKENS):
    tokens = message_tokens(messages)
    if tokens > input_limit(output):
        raise ValueError("The request exceeds the model context budget. Narrow the question or select a smaller section.")
    return tokens

def fit_history(messages, allowance=3000):
    kept, used = [], 0
    for message in reversed(messages):
        cost = estimate(message["content"]) + 16
        if kept and used + cost > allowance:
            break
        kept.append(message)
        used += cost
    return list(reversed(kept))
