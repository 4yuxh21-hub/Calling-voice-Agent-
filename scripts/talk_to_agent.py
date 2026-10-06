"""Talk to the voice agent with your own microphone and speakers.

A "software telephone": captures mic audio, streams it to the agent over the
Exotel Media Streams WebSocket protocol, and plays the agent's replies. Barge-in
works - when the agent hears you it sends a `clear` event and playback stops.

Wear HEADPHONES: without echo cancellation the mic picks up the agent's own
voice from your speakers and it will interrupt itself (the PSTN echo problem,
see PLAN.md section 2).

Run (agent server must be up):
  python scripts\\talk_to_agent.py
End the call with Ctrl+C.
"""

import argparse
import asyncio
import base64
import json
import random
import time
from collections import deque

import numpy as np
import sounddevice as sd
import soundfile as sf

RATE = 8000
CHUNK_SAMPLES = 160  # 20ms of mono 16-bit audio
CHUNK_BYTES = CHUNK_SAMPLES * 2


class Softphone:
    def __init__(self, save_reply: str | None):
        self.call_sid = f"talk-{random.randint(100000, 999999)}"
        self.stream_sid = f"stream-{self.call_sid}"
        self.seq = 0
        self.playback: deque[np.ndarray] = deque()
        self.mic_queue: asyncio.Queue | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.bot_chunks = 0
        self.user_chunks = 0
        self.reply_pcm = bytearray()
        self.save_reply = save_reply

    # --- audio callbacks (run on PortAudio's thread) ---

    def mic_callback(self, indata, frames, time_info, status):
        chunk = indata[:, 0].tobytes()
        self.user_chunks += 1
        if self.loop and self.mic_queue:
            self.loop.call_soon_threadsafe(self.mic_queue.put_nowait, chunk)

    def out_callback(self, outdata, frames, time_info, status):
        need = frames
        filled = 0
        while filled < need and self.playback:
            piece = self.playback.popleft()
            take = min(len(piece) // 2, need - filled)
            samples = np.frombuffer(piece[: take * 2], dtype=np.int16)
            outdata[filled : filled + take, 0] = samples
            filled += take
        if filled < need:
            outdata[filled:, 0] = 0

    # --- network ---

    def media_event(self, payload: bytes) -> str:
        self.seq += 1
        return json.dumps(
            {
                "event": "media",
                "sequenceNumber": str(self.seq),
                "streamSid": self.stream_sid,
                "media": {
                    "track": "inbound",
                    "chunk": self.seq,
                    "timestamp": self.seq * 20,
                    "payload": base64.b64encode(payload).decode("ascii"),
                },
            }
        )

    async def sender(self, ws):
        while True:
            chunk = await self.mic_queue.get()
            await ws.send(self.media_event(chunk))

    async def receiver(self, ws):
        async for raw in ws:
            try:
                message = json.loads(raw)
            except (TypeError, ValueError):
                continue
            event = message.get("event")
            if event == "media":
                pcm = base64.b64decode(message["media"]["payload"])
                self.bot_chunks += 1
                self.reply_pcm.extend(pcm)
                # split into <=CHUNK_BYTES pieces for the playback deque
                for off in range(0, len(pcm), CHUNK_BYTES):
                    self.playback.append(bytes(pcm[off : off + CHUNK_BYTES]))
            elif event == "clear":
                # agent heard the user and is barging in: stop playback now
                self.playback.clear()

    async def run(self, url: str, caller: str, duration: float | None = None):
        import websockets

        self.loop = asyncio.get_running_loop()
        self.mic_queue = asyncio.Queue(maxsize=100)

        async with websockets.connect(url, max_size=8 * 1024 * 1024) as ws:
            await ws.send(
                json.dumps(
                    {
                        "event": "start",
                        "sequenceNumber": "1",
                        "streamSid": self.stream_sid,
                        "start": {
                            "streamSid": self.stream_sid,
                            "callSid": self.call_sid,
                            "customParameters": {"caller": caller},
                        },
                    }
                )
            )

            mic = sd.InputStream(
                samplerate=RATE,
                channels=1,
                dtype="int16",
                blocksize=CHUNK_SAMPLES,
                callback=self.mic_callback,
            )
            out = sd.OutputStream(
                samplerate=RATE,
                channels=1,
                dtype="int16",
                blocksize=CHUNK_SAMPLES,
                callback=self.out_callback,
            )

            recv_task = asyncio.create_task(self.receiver(ws))
            send_task = asyncio.create_task(self.sender(ws))

            with mic, out:
                print(f"Call connected (call_sid={self.call_sid}). Speak - Ctrl+C to hang up.")
                print("Wear headphones! Without them the agent hears itself through your mic.")
                try:
                    if duration:
                        await asyncio.sleep(duration)
                    else:
                        await asyncio.Event().wait()  # until Ctrl+C
                except asyncio.CancelledError:
                    pass

            send_task.cancel()
            recv_task.cancel()
            try:
                await ws.send(json.dumps({"event": "stop", "streamSid": self.stream_sid}))
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description="Talk to the voice agent (softphone)")
    parser.add_argument(
        "--url", default="ws://localhost:8080/ws/exotel?did=+9111234567890"
    )
    parser.add_argument("--caller", default="+919876543210")
    parser.add_argument("--save-reply", help="save the agent's audio to this WAV file")
    parser.add_argument("--duration", type=float, help="auto-hangup after N seconds")
    args = parser.parse_args()

    phone = Softphone(args.save_reply)
    try:
        asyncio.run(phone.run(args.url, args.caller, args.duration))
    except KeyboardInterrupt:
        pass
    minutes = phone.user_chunks * 0.02 / 60
    print(f"\nCall over. You: {minutes:.1f} min | agent audio chunks: {phone.bot_chunks}")
    if args.save_reply and phone.reply_pcm:
        sf.write(
            args.save_reply,
            np.frombuffer(bytes(phone.reply_pcm), dtype=np.int16),
            RATE,
            subtype="PCM_16",
        )
        print(f"agent audio saved -> {args.save_reply}")


if __name__ == "__main__":
    main()
