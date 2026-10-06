"""Scripted mock STT/LLM/TTS so the full pipeline runs with zero API keys.

- MockSTT: energy-based utterance detection over pipeline audio; emits the
  next transcript from the benchmark scenario script (passed via the carrier
  `start` event custom parameters). Finals are `finalized=True` so turn end
  is deterministic.
- MockLLM: canned replies; triggers a real get_order_status tool call when the
  user mentions an order, so the whole tool path (filler, binding, callbacks)
  is exercised without a real LLM.
- MockTTS: synthesizes a soft tone proportional to text length, exercising the
  audio-out path, interruptions and TTFB metrics.

Note: each service gets a complete Pipecat Settings object (all fields filled)
- the base classes validate that on StartFrame.
"""

import asyncio
import dataclasses
import uuid
from collections import deque
from datetime import datetime, timezone

import numpy as np
from loguru import logger
from pipecat.frames.frames import (
    FunctionCallFromLLM,
    InterimTranscriptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
)
from pipecat.processors.frame_processor import Frame, FrameDirection
from pipecat.services.llm_service import LLMService
from pipecat.services.settings import LLMSettings, STTSettings, TTSSettings
from pipecat.services.stt_service import STTService
from pipecat.services.tts_service import TTSService

from va.session import CallSession


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _full_settings(cls, **kwargs):
    """Fill every Settings field (None where unsupported) so validate_complete passes."""
    names = [f.name for f in dataclasses.fields(cls)]
    return cls(**{name: kwargs.get(name) for name in names})


class MockSTTService(STTService):
    def __init__(self, *, session: CallSession, transcripts: list[str], **kwargs):
        super().__init__(settings=_full_settings(STTSettings, model="mock-stt", language=None),
                         **kwargs)
        self._session = session
        self._pending = deque(transcripts)
        self._state = "idle"
        self._text = ""
        self._speech_ms = 0.0
        self._silence_ms = 0.0
        self._interim_sent = False

    async def run_stt(self, audio: bytes):
        if not audio:
            yield None
            return
        samples = np.frombuffer(audio, dtype=np.int16).astype(np.float32)
        rms = float(np.sqrt(np.mean(samples**2))) if samples.size else 0.0
        chunk_ms = len(samples) / 8000.0 * 1000.0
        speaking = rms > 150.0

        if speaking:
            if self._state == "idle":
                text = self._pending.popleft() if self._pending else None
                if text is None:
                    # Script exhausted (residual/overlapping audio): stay idle.
                    yield None
                    return
                self._text = text
                self._state = "speech"
                self._speech_ms = 0.0
                self._silence_ms = 0.0
                self._interim_sent = False
                logger.debug("MockSTT utterance start -> {!r}", self._text)
            self._speech_ms += chunk_ms
            self._silence_ms = 0.0
            if not self._interim_sent and self._speech_ms >= 120:
                self._interim_sent = True
                yield InterimTranscriptionFrame(text=self._text, user_id="", timestamp=_now_iso())
        elif self._state == "speech":
            self._silence_ms += chunk_ms
            # 650ms: long enough that natural sentence pauses inside one
            # fixture don't split it into multiple utterances, short enough
            # that fixture boundaries stay clean.
            if self._silence_ms >= 650.0:
                self._state = "idle"
                logger.debug("MockSTT utterance end -> {!r}", self._text)
                yield TranscriptionFrame(
                    text=self._text, user_id="", timestamp=_now_iso(), finalized=True
                )
        yield None


class MockLLMService(LLMService):
    def __init__(self, *, session: CallSession, app_settings=None, **kwargs):
        super().__init__(settings=_full_settings(LLMSettings, model="mock-llm"), **kwargs)
        self._session = session
        self._app_settings = app_settings

    async def process_frame(self, frame, direction):
        # Vendor LLM services override process_frame with a fall-through push
        # (LLMService.process_frame has none); without it, StartFrame never
        # reaches the rest of the pipeline and startup deadlocks.
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame):
            await self._process_context(frame.context)
        else:
            await self.push_frame(frame, direction)

    async def _process_context(self, context):
        await self.push_frame(LLMFullResponseStartFrame())
        try:
            await self.start_ttfb_metrics()
            await asyncio.sleep(0.12)  # simulated time-to-first-token
            await self.stop_ttfb_metrics()

            messages = context.messages
            last = messages[-1] if messages else {}
            role = last.get("role", "")
            content = str(last.get("content", ""))

            # Stateless check for an unanswered tool result: the user may have
            # kept talking while the tool ran, so the result isn't always the
            # newest message. Count results vs. assistant replies that already
            # answered one ("shipped").
            tool_results = sum(
                1 for m in messages if m.get("role") in ("tool", "function")
            )
            answered = sum(
                1
                for m in messages
                if m.get("role") == "assistant" and "shipped" in str(m.get("content", "")).lower()
            )
            has_unanswered = tool_results > answered

            if role in ("tool", "function") or has_unanswered:
                reply = (
                    "Good news - your order has shipped and will arrive by Friday. "
                    "Is there anything else I can help you with?"
                )
                if "bye" in content.lower() or "thank" in content.lower():
                    reply = (
                        "Good news - your order has shipped and will arrive by Friday. "
                        "Thank you for calling, have a great day!"
                    )
                await self._push_llm_text(reply)
            elif "order" in content.lower():
                await self._push_llm_text("Sure, let me check that for you.")
                await self.run_function_calls(
                    [
                        FunctionCallFromLLM(
                            function_name="get_order_status",
                            tool_call_id=f"mock-{uuid.uuid4().hex[:10]}",
                            arguments={"order_id": "12345"},
                            context=None,
                        )
                    ]
                )
                return  # the tool-result turn re-enters _process_context
            elif "bye" in content.lower() or "thank" in content.lower():
                reply = "Thank you for calling. Have a great day!"
                await self._push_llm_text(reply)
            else:
                reply = "Thanks for calling the demo line. How can I help you today?"
                await self._push_llm_text(reply)
        finally:
            await self.push_frame(LLMFullResponseEndFrame())


class MockTTSService(TTSService):
    def __init__(self, *, mock_rate: int = 8000, **kwargs):
        super().__init__(settings=_full_settings(TTSSettings, model="mock-tts"), **kwargs)
        self._mock_rate = mock_rate

    async def run_tts(self, text: str, context_id: str):
        duration = min(0.3 + 0.045 * len(text), 12.0)
        rate = self.sample_rate or self._mock_rate
        count = int(duration * rate)
        t = np.linspace(0, duration, count, endpoint=False)
        # soft 220Hz tone with slow vibrato - enough energy to exercise the
        # audio-out path and the simulator's receive loop
        wave = 0.35 * np.sin(2 * np.pi * 220 * t) * (1 + 0.2 * np.sin(2 * np.pi * 4 * t))
        pcm = (wave * 12000).astype(np.int16).tobytes()
        yield TTSAudioRawFrame(audio=pcm, sample_rate=rate, num_channels=1)
        yield None
