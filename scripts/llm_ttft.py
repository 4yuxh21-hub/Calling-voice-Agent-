"""Temporary: measure Gemini TTFT (streaming) for flash vs flash-lite on Vertex express."""

import asyncio
import os
import time

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

KEY = os.getenv("GOOGLE_API_KEY", "")
client = genai.Client(vertexai=True, api_key=KEY)

CONTEXT = [
    "Hello! Thanks for calling Demo Store. How can I help you today?",
    "Hello. Thank you for calling.",
    "And",
    "I want to help regarding matters.",
]


async def ttft(model: str, label: str):
    contents = [types.Content(role="user", parts=[types.Part(text=t)]) for t in CONTEXT]
    start = time.monotonic()
    try:
        stream = await client.aio.models.generate_content_stream(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(temperature=0.6, thinking_config=types.ThinkingConfig(thinking_budget=0)),
        )
        first = None
        text = ""
        async for chunk in stream:
            if first is None:
                first = time.monotonic() - start
            text += chunk.text or ""
        total = time.monotonic() - start
        print(f"{label}: TTFT={first:.2f}s total={total:.2f}s reply={text[:70]!r}")
    except Exception as e:
        print(f"{label}: FAILED {type(e).__name__}: {str(e)[:150]}")


async def main():
    await ttft("gemini-2.5-flash", "flash #1")
    await ttft("gemini-2.5-flash", "flash #2")
    await ttft("gemini-2.5-flash-lite", "lite  #1")
    await ttft("gemini-2.5-flash-lite", "lite  #2")


asyncio.run(main())
