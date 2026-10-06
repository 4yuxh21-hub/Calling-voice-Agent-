"""Per-call metrics + transcript ledger (JSONL) for latency and cost tracking."""

import json
import time
from pathlib import Path

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    FunctionCallInProgressFrame,
    MetricsFrame,
    TranscriptionFrame,
    TTSTextFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed

from va.session import CallSession


class CallLedger:
    """Appends one JSON line per event to data/ledger/<call_id>.jsonl."""

    def __init__(self, data_dir: Path, session: CallSession):
        self._session = session
        ledger_dir = data_dir / "ledger"
        ledger_dir.mkdir(parents=True, exist_ok=True)
        self._path = ledger_dir / f"{session.call_id}.jsonl"

    def write(self, event: dict):
        event = {"ts": time.time(), "call_id": self._session.call_id, **event}
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        except OSError as exc:
            logger.warning("Ledger write failed: {}", exc)

    def call_started(self):
        self.write(
            {
                "event": "call_started",
                "carrier": self._session.carrier,
                "tenant": self._session.tenant.tenant_id,
                "caller_id": self._session.caller_id,
            }
        )

    def call_ended(self, reason: str):
        self.write({"event": "call_ended", "reason": reason})


class MetricsObserver(BaseObserver):
    """Read-only observer: per-turn TTFB/usage metrics + transcripts -> ledger."""

    def __init__(self, ledger: CallLedger):
        super().__init__(observe_every_push=False)
        self._ledger = ledger

    async def on_push_frame(self, data: FramePushed):
        frame: Frame = data.frame

        if isinstance(frame, MetricsFrame):
            for metric in frame.data:
                self._ledger.write(
                    {"event": "metric", "metric": metric.__class__.__name__,
                     **metric.model_dump()}
                )
        elif isinstance(frame, TranscriptionFrame):
            self._ledger.write(
                {"event": "user_transcript", "text": frame.text, "finalized": frame.finalized}
            )
        elif isinstance(frame, TTSTextFrame):
            self._ledger.write({"event": "bot_text", "text": frame.text})
        elif isinstance(frame, FunctionCallInProgressFrame):
            self._ledger.write(
                {"event": "tool_call", "function": frame.function_name,
                 "arguments": dict(frame.arguments)}
            )
