"""Per-call session state shared across pipeline components."""

from dataclasses import dataclass, field

from loguru import logger

from va.config.tenants import TenantConfig


@dataclass
class CallSession:
    call_id: str
    carrier: str
    stream_sid: str
    call_sid: str | None
    tenant: TenantConfig
    # Verified caller identity from the carrier (SIP/`start` event params).
    # Tool handlers must bind sensitive args to this, never to LLM output.
    caller_id: str = "unknown"
    # Ordered transcripts the mock STT emits when a benchmark scenario script
    # is supplied via the carrier `start` event custom parameters.
    script_transcripts: list[str] = field(default_factory=list)
    # Populated at call end for the ledger.
    ended_reason: str = ""

    def next_script_transcript(self) -> str | None:
        if self.script_transcripts:
            return self.script_transcripts.pop(0)
        return None


def parse_caller_id(custom: dict, start: dict | None = None) -> str:
    """Extract the caller number from the carrier start event.

    Checks custom parameters first (applet-provided), then the carrier's own
    `from` field (verified by the network - this is the value tool bindings
    should trust).
    """
    for key in ("caller", "from", "From", "caller_id", "CustomerNumber"):
        value = custom.get(key)
        if value:
            return str(value)
    if start:
        for key in ("from", "From"):
            value = start.get(key)
            if value:
                return str(value)
    logger.warning("No caller id found in carrier start event: {}", list(custom))
    return "unknown"
