"""Audition a Cartesia voice: synthesize a WAV sample you can play.

Reads CARTESIA_API_KEY from .env. Examples:
  python scripts\\voice_sample.py --text "Namaste! Aapki kaise madad kar sakta hoon?" --language hi
  python scripts\\voice_sample.py --voice <uuid> --text "Hello there" --out my_sample.wav
"""

import argparse
import os

import httpx
from dotenv import load_dotenv

load_dotenv()


def main():
    parser = argparse.ArgumentParser(description="Cartesia voice sampler")
    parser.add_argument("--voice", default=os.getenv("VA_TTS_VOICE_ID", ""))
    parser.add_argument("--text", default="Hello! Thanks for calling Demo Store. How can I help you today?")
    parser.add_argument("--language", default="en", choices=["en", "hi"])
    parser.add_argument("--model", default="sonic-3.6")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    api_key = os.getenv("CARTESIA_API_KEY", "")
    if not api_key or not args.voice:
        raise SystemExit("CARTESIA_API_KEY missing in .env, or no --voice given")

    out = args.out or f"fixtures/audio/_sample_{args.voice[:8]}_{args.language}.wav"
    with httpx.Client(timeout=60) as client:
        r = client.post(
            "https://api.cartesia.ai/tts/bytes",
            headers={
                "X-API-Key": api_key,
                "Cartesia-Version": "2026-08-14",
                "Content-Type": "application/json",
            },
            json={
                "model_id": args.model,
                "transcript": args.text,
                "voice": {"mode": "id", "id": args.voice},
                "language": args.language,
                "output_format": {"container": "wav", "encoding": "pcm_s16le", "sample_rate": 8000},
            },
        )
    if r.status_code == 200 and r.content[:4] == b"RIFF":
        with open(out, "wb") as f:
            f.write(r.content)
        print(f"saved -> {out}")
    else:
        raise SystemExit(f"HTTP {r.status_code}: {r.text[:300]}")


if __name__ == "__main__":
    main()
