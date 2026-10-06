"""Circuit-breaker guards: silence timeout and absolute call duration cap.

A dropped PSTN call can't be resumed, so these guards exist to stop infinite
billing loops (voicemail boxes, desk drops, dead air) rather than to recover.

- Silence: after `silence_timeout_secs` without user activity, ask "Are you
  still there?"; if the silence continues past a grace window, end the call.
- Duration: hard cap at `max_call_duration_secs`; say goodbye and end.
"""

import asyncio
import time

from loguru import logger
from pipecat.frames.frames import (
    EndWorkerFrame,
    Frame,
    StartFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class GuardsProcessor(FrameProcessor):
    def __init__(
        self,
        *,
        silence_timeout_secs: float = 45.0,
        silence_grace_secs: float = 30.0,
        max_call_duration_secs: float = 900.0,
        still_there_prompt: str = "Are you still there?",
        goodbye_prompt: str = "Thank you for calling. Goodbye!",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._silence_timeout = silence_timeout_secs
        self._silence_grace = silence_grace_secs
        self._max_duration = max_call_duration_secs
        self._still_there = still_there_prompt
        self._goodbye = goodbye_prompt

        self._start_monotonic = 0.0
        self._last_user_activity = 0.0
        self._silence_warned = False
        self._ending = False
        self._monitor_task: asyncio.Task | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            self._start_monotonic = time.monotonic()
            self._last_user_activity = self._start_monotonic
            if not self._monitor_task:
                self._monitor_task = self.create_task(self._monitor())
        elif isinstance(frame, (UserStartedSpeakingFrame, TranscriptionFrame)):
            self._last_user_activity = time.monotonic()
            self._silence_warned = False

        await self.push_frame(frame, direction)

    async def cleanup(self):
        if self._monitor_task:
            await self.cancel_task(self._monitor_task)
            self._monitor_task = None
        await super().cleanup()

    async def _monitor(self):
        while True:
            await asyncio.sleep(5.0)
            now = time.monotonic()
            if self._ending:
                return

            if now - self._start_monotonic >= self._max_duration:
                await self._end_call("duration_cap")
                return

            silent_for = now - self._last_user_activity
            if silent_for >= self._silence_timeout + self._silence_grace:
                await self._end_call("silence_timeout")
                return
            if silent_for >= self._silence_timeout and not self._silence_warned:
                self._silence_warned = True
                logger.info("Guard: {}s of silence, checking in with caller", int(silent_for))
                await self.queue_frame(self._make_speak(self._still_there))

    async def _end_call(self, reason: str):
        self._ending = True
        logger.warning("Guard ending call: {}", reason)
        await self.queue_frame(self._make_speak(self._goodbye))
        await asyncio.sleep(2.0)  # let the goodbye start playing before teardown
        await self.push_frame(EndWorkerFrame())

    def _make_speak(self, text: str) -> Frame:
        return TTSSpeakFrame(text=text)
