"""Carrier -> serializer factory. Exotel default, Telnyx for international.

Audio formats (verified against the installed pipecat 1.12 serializers):
- Exotel Media Streams: base64-encoded PCM16 LE, 8 kHz mono (NOT mu-law).
- Telnyx: base64-encoded mu-law (PCMU), 8 kHz mono.
The serializers convert to/from pipeline PCM internally.
"""

from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.serializers.exotel import ExotelFrameSerializer
from pipecat.serializers.telnyx import TelnyxFrameSerializer


def build_serializer(
    carrier: str,
    stream_sid: str,
    call_sid: str | None = None,
    sample_rate: int = 8000,
    telnyx_api_key: str | None = None,
) -> FrameSerializer:
    if carrier == "exotel":
        return ExotelFrameSerializer(
            stream_sid=stream_sid,
            call_sid=call_sid,
            params=ExotelFrameSerializer.InputParams(
                exotel_sample_rate=8000, sample_rate=sample_rate
            ),
        )
    if carrier == "telnyx":
        # With credentials the serializer can hang the call up on EndFrame;
        # without them auto-hang-up is disabled and teardown happens via the
        # carrier API instead.
        return TelnyxFrameSerializer(
            stream_id=stream_sid,
            outbound_encoding="PCMU",
            inbound_encoding="PCMU",
            call_control_id=call_sid,
            api_key=telnyx_api_key,
            params=TelnyxFrameSerializer.InputParams(
                telnyx_sample_rate=8000,
                sample_rate=sample_rate,
                auto_hang_up=bool(telnyx_api_key and call_sid),
            ),
        )
    raise ValueError(f"Unknown carrier: {carrier!r}")
