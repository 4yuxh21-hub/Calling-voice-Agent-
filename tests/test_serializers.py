"""Exotel serializer round-trip against the installed pipecat 1.12 code."""

import base64
import json

import pytest
from pipecat.frames.frames import InputAudioRawFrame, OutputAudioRawFrame
from pipecat.serializers.exotel import ExotelFrameSerializer
from pipecat.serializers.telnyx import TelnyxFrameSerializer


@pytest.fixture
def exotel():
    serializer = ExotelFrameSerializer(stream_sid="stream-1", call_sid="call-1")
    # setup() would set this from the pipeline's audio_in_sample_rate
    serializer._sample_rate = 8000
    return serializer


def _pcm(seconds: float = 0.02) -> bytes:
    return bytes(range(256)) * int(seconds * 8000 * 2 / 256)


async def test_exotel_serialize_output_audio(exotel):
    pcm = _pcm()
    payload = await exotel.serialize(
        OutputAudioRawFrame(audio=pcm, sample_rate=8000, num_channels=1)
    )
    assert isinstance(payload, str)
    message = json.loads(payload)
    assert message["event"] == "media"
    assert message["stream_sid"] == "stream-1"
    assert base64.b64decode(message["media"]["payload"])[:16] == pcm[:16]


async def test_exotel_deserialize_media(exotel):
    pcm = _pcm()
    raw = json.dumps(
        {"event": "media", "media": {"payload": base64.b64encode(pcm).decode("ascii")}}
    )
    frame = await exotel.deserialize(raw)
    assert isinstance(frame, InputAudioRawFrame)
    assert frame.sample_rate == 8000
    assert frame.num_channels == 1
    assert len(frame.audio) > 0


async def test_exotel_ignores_non_media_events(exotel):
    assert await exotel.deserialize(json.dumps({"event": "start", "start": {}})) is None
    assert await exotel.deserialize(json.dumps({"event": "stop"})) is None


async def test_exotel_serializes_clear_on_interruption(exotel):
    from pipecat.frames.frames import InterruptionFrame

    payload = await exotel.serialize(InterruptionFrame())
    message = json.loads(payload)
    assert message == {"event": "clear", "stream_sid": "stream-1"}


def test_telnyx_construction():
    serializer = TelnyxFrameSerializer(
        stream_id="stream-2",
        outbound_encoding="PCMU",
        inbound_encoding="PCMU",
        call_control_id="cc-1",
        api_key="test-key",
    )
    assert serializer is not None


def test_telnyx_without_credentials_disables_auto_hangup():
    serializer = TelnyxFrameSerializer(
        stream_id="stream-2",
        outbound_encoding="PCMU",
        inbound_encoding="PCMU",
        params=TelnyxFrameSerializer.InputParams(auto_hang_up=False),
    )
    assert serializer is not None


def test_factory_builds_both_carriers():
    from va.telephony import build_serializer

    assert isinstance(build_serializer("exotel", "s"), ExotelFrameSerializer)
    assert isinstance(build_serializer("telnyx", "s"), TelnyxFrameSerializer)
    with pytest.raises(ValueError):
        build_serializer("unknown", "s")
