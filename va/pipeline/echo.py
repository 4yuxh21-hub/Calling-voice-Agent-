"""Echo processor for the M1 bidirectional-audio gate.

The output transport only writes `OutputAudioRawFrame`, while the input
transport produces `InputAudioRawFrame`; this processor converts between them
so `pipeline = [transport.input(), EchoProcessor(), transport.output()]`
loops carrier audio straight back.
"""

from pipecat.frames.frames import Frame, InputAudioRawFrame, OutputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class EchoProcessor(FrameProcessor):
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            await self.push_frame(
                OutputAudioRawFrame(
                    audio=frame.audio,
                    sample_rate=frame.sample_rate,
                    num_channels=frame.num_channels,
                )
            )
        else:
            await self.push_frame(frame, direction)
