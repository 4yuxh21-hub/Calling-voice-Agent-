"""Exotel Media Streams simulator: drives the bot over WebSocket without PSTN.

Speaks the same JSON protocol the Exotel serializer consumes (base64 PCM16 LE
@ 8kHz `media` events), replays scenario audio, and measures client-side
per-turn latency (end of utterance audio -> first bot audio back).

Run:
  python scripts/simulate_carrier.py --url ws://localhost:8080/ws/exotel --scenario en_order
  python scripts/simulate_carrier.py --url ws://localhost:8080/ws/exotel --audio fixtures/audio/en_order.wav
Prints a JSON summary on stdout (the benchmark harness parses it).
"""

import argparse
import asyncio
import base64
import json
import random
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr
import websockets

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_DIR = PROJECT_ROOT / "fixtures" / "scenarios"

CHUNK_BYTES = 320  # 20ms of PCM16 mono @ 8kHz


def load_audio_8k(path: Path) -> bytes:
    data, sr = sf.read(path, dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != 8000:
        data = soxr.resample(data, sr, 8000)
    return (np.clip(data, -1.0, 1.0) * 32767).astype(np.int16).tobytes()


async def _receiver(ws, stats):
    """Consume bot->carrier frames; record audio chunk timestamps."""
    try:
        async for raw in ws:
            try:
                message = json.loads(raw)
            except (TypeError, ValueError):
                continue
            event = message.get("event")
            if event == "media":
                now = time.monotonic()
                stats["audio_chunks"] += 1
                if stats["first_audio_ts"] is None:
                    stats["first_audio_ts"] = now
                stats["last_audio_ts"] = now
                stats["last_audio_mono"] = now
                stats["audio_silence_gap"] = False
            elif event == "clear":
                stats["clears"] += 1
            elif event == "stop":
                break
    except websockets.ConnectionClosed:
        pass


async def _wait_for_reply_audio(stats, timeout_s: float, chunks_before: int):
    """Wait until bot audio flows after `chunks_before`; return ms, or False on timeout."""
    start = time.monotonic()
    while time.monotonic() - start < timeout_s:
        if stats["audio_chunks"] > chunks_before:
            return (time.monotonic() - start) * 1000.0
        await asyncio.sleep(0.01)
    return False


async def _wait_for_bot_quiet(stats, max_wait_s: float, quiet_s: float):
    """Wait until the bot has spoken and then stayed quiet for `quiet_s` (greeting)."""
    start = time.monotonic()
    spoke_at = None
    while time.monotonic() - start < max_wait_s:
        now = time.monotonic()
        if stats["audio_chunks"] > 0:
            if spoke_at is None:
                spoke_at = now
            if now - stats.get("last_audio_mono", 0) >= quiet_s:
                return
        elif spoke_at is not None:
            return
        await asyncio.sleep(0.05)


async def run_simulation(
    url: str,
    scenario_id: str | None = None,
    single_audio: Path | None = None,
    caller: str = "+919876543210",
    turn_gap_s: float = 0.8,
    reply_timeout_s: float = 15.0,
) -> dict:
    call_sid = f"sim-{random.randint(100000, 999999)}"
    stream_sid = f"stream-{call_sid}"

    if scenario_id:
        scenario = json.loads((SCENARIOS_DIR / f"{scenario_id}.json").read_text(encoding="utf-8"))
        utterances = scenario["utterances"]
    else:
        utterances = [{"audio": str(single_audio), "text": "(single file)"}]

    stats = {
        "audio_chunks": 0,
        "clears": 0,
        "first_audio_ts": None,
        "last_audio_ts": None,
        "awaiting_since": 0.0,
    }
    turns = []

    async with websockets.connect(url, max_size=8 * 1024 * 1024) as ws:
        receiver = asyncio.create_task(_receiver(ws, stats))
        await ws.send(
            json.dumps(
                {
                    "event": "start",
                    "sequenceNumber": "1",
                    "streamSid": stream_sid,
                    "start": {
                        "streamSid": stream_sid,
                        "callSid": call_sid,
                        "customParameters": {"caller": caller, "script": scenario_id or ""},
                    },
                }
            )
        )
        await asyncio.sleep(0.3)

        seq = 1
        for i, utterance in enumerate(utterances):
            if i == 0:
                # Real callers wait out the greeting; also keeps the first
                # words from being lost to barge-in overlap.
                await _wait_for_bot_quiet(stats, max_wait_s=10.0, quiet_s=1.0)

            audio_path = Path(utterance["audio"])
            if not audio_path.is_absolute():
                audio_path = PROJECT_ROOT / "fixtures" / "audio" / audio_path.name
            pcm = load_audio_8k(audio_path)

            chunks_before = stats["audio_chunks"]
            send_start = time.time()
            sent_ms = 0.0
            for offset in range(0, len(pcm), CHUNK_BYTES):
                seq += 1
                await ws.send(
                    json.dumps(
                        {
                            "event": "media",
                            "sequenceNumber": str(seq),
                            "streamSid": stream_sid,
                            "media": {
                                "track": "inbound",
                                "chunk": seq,
                                "timestamp": sent_ms,
                                "payload": base64.b64encode(
                                    pcm[offset : offset + CHUNK_BYTES]
                                ).decode("ascii"),
                            },
                        }
                    )
                )
                sent_ms += 20.0
                await asyncio.sleep(0.02)

            # Latency = end of the utterance's audio -> first bot audio after
            # that point (bot audio during the utterance is normal barge-in /
            # turn-taking overlap and belongs to earlier turns).
            answered_ms = await _wait_for_reply_audio(stats, reply_timeout_s, chunks_before)
            turns.append(
                {
                    "turn": i + 1,
                    "audio": utterance["audio"],
                    "text": utterance.get("text", ""),
                    "sent_ms": round(sent_ms),
                    "send_start": send_start,
                    "answered": answered_ms is not False,
                }
            )
            await asyncio.sleep(turn_gap_s)

        await ws.send(json.dumps({"event": "stop", "streamSid": stream_sid}))
        await asyncio.sleep(0.5)
        receiver.cancel()

    return {
        "call_sid": call_sid,
        "url": url,
        "scenario": scenario_id,
        "turns": turns,
        "bot_audio_chunks": stats["audio_chunks"],
        "clear_events": stats["clears"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Exotel Media Streams simulator")
    parser.add_argument("--url", default="ws://localhost:8080/ws/exotel")
    parser.add_argument("--scenario", help="scenario id in fixtures/scenarios/<id>.json")
    parser.add_argument("--audio", help="single audio file to send (no scenario)")
    parser.add_argument("--caller", default="+919876543210")
    args = parser.parse_args()

    if not args.scenario and not args.audio:
        parser.error("provide --scenario or --audio")

    summary = asyncio.run(
        run_simulation(args.url, args.scenario, Path(args.audio) if args.audio else None, args.caller)
    )
    print(json.dumps(summary, indent=2))
    missing = [t["turn"] for t in summary["turns"] if not t["answered"]]
    if missing:
        print(f"WARNING: no bot audio for turns {missing}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
