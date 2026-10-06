"""Per-connection bot assembly: transport -> pipeline -> task.

One WebSocket connection = one call = one pipeline. In production (M4) each
call runs in its own container/process and scales horizontally; this module
keeps the per-call isolation so that deployment model is a config change,
not a refactor.
"""

import asyncio
import uuid

from loguru import logger
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.observers.base_observer import BaseObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from pipecat.turns.user_start import (
    MinWordsUserTurnStartStrategy,
    TranscriptionUserTurnStartStrategy,
    VADUserTurnStartStrategy,
)
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.utils.types import NOT_GIVEN
from pipecat.workers.runner import WorkerRunner

from va.config.settings import Settings
from va.config.tenants import TenantConfig
from va.mocks.mock_services import MockLLMService, MockSTTService, MockTTSService
from va.observability import CallLedger, MetricsObserver
from va.pipeline.echo import EchoProcessor
from va.pipeline.guards import GuardsProcessor
from va.pipeline.services import build_llm, build_stt, build_tts
from va.session import CallSession
from va.tools.engine import build_tool_schemas, register_tools
from va.telephony import build_serializer


class _PipelineStartedGate(BaseObserver):
    """Sets an event when the pipeline has fully started (see greeting gate below)."""

    def __init__(self, event: asyncio.Event):
        super().__init__(observe_every_push=False)
        self._event = event

    async def on_pipeline_started(self):
        self._event.set()


async def run_bot(
    websocket,
    *,
    carrier: str,
    stream_sid: str,
    call_sid: str | None,
    tenant: TenantConfig,
    caller_id: str,
    script_transcripts: list[str] | None = None,
    settings: Settings,
) -> None:
    session = CallSession(
        call_id=call_sid or f"call-{uuid.uuid4().hex[:12]}",
        carrier=carrier,
        stream_sid=stream_sid,
        call_sid=call_sid,
        tenant=tenant,
        caller_id=caller_id,
        script_transcripts=list(script_transcripts or []),
    )
    logger.info(
        "Call start: {} carrier={} tenant={} caller={}",
        session.call_id, carrier, tenant.tenant_id, caller_id,
    )
    ledger = CallLedger(settings.data_path, session)
    ledger.call_started()

    serializer = build_serializer(carrier, stream_sid, call_sid, settings.sample_rate)
    transport = FastAPIWebsocketTransport(
        websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=settings.sample_rate,
            audio_out_sample_rate=settings.sample_rate,
            serializer=serializer,
            allowed_origins=[],  # carrier media streams send no Origin header
        ),
    )

    if settings.mode == "echo":
        pipeline = Pipeline([transport.input(), EchoProcessor(), transport.output()])
        observers = []
        logger.info("Echo mode: bidirectional audio loop (M1 gate)")
    else:
        observers = [MetricsObserver(ledger)]
        stt = (
            MockSTTService(session=session, transcripts=session.script_transcripts)
            if settings.use_mock_stt
            else build_stt(settings)
        )
        llm = (
            MockLLMService(session=session, app_settings=settings)
            if settings.use_mock_llm
            else build_llm(settings)
        )
        tts = (
            MockTTSService(mock_rate=settings.sample_rate)
            if settings.use_mock_tts
            else build_tts(settings, tenant)
        )

        tools_schema = build_tool_schemas(tenant)
        context = LLMContext(
            messages=[
                {"role": "system", "content": tenant.system_prompt},
                {"role": "assistant", "content": tenant.greeting},
            ],
            tools=tools_schema if tools_schema is not None else NOT_GIVEN,
        )

        for name, handler in register_tools(tenant, session, settings).items():
            llm.register_function(name, handler)

        user_params = LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=settings.vad_stop_secs)),
            user_turn_strategies=UserTurnStrategies(
                start=[
                    VADUserTurnStartStrategy(),
                    TranscriptionUserTurnStartStrategy(),
                    # 1 word starts a turn while the bot is silent; N words to interrupt
                    MinWordsUserTurnStartStrategy(min_words=settings.min_start_words),
                ],
                stop=[TurnAnalyzerUserTurnStopStrategy(turn_analyzer=LocalSmartTurnAnalyzerV3())],
            ),
        )
        user_agg, assistant_agg = LLMContextAggregatorPair(context, user_params=user_params)
        guards = GuardsProcessor(
            silence_timeout_secs=settings.silence_timeout_secs,
            max_call_duration_secs=settings.max_call_duration_secs,
        )

        pipeline = Pipeline(
            [
                transport.input(),
                guards,
                stt,
                user_agg,
                llm,
                tts,
                transport.output(),
                assistant_agg,
            ]
        )

    # Gate the greeting on actual pipeline start. Queuing it in
    # on_client_connected lands before StartFrame has propagated; the incoming
    # caller audio then interrupts a bot that never officially started, and
    # the pipeline deadlocks until the start timeout kills the call.
    pipeline_started = asyncio.Event()
    started_gate = _PipelineStartedGate(pipeline_started)
    observers = [*observers, started_gate]

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=settings.sample_rate,
            audio_out_sample_rate=settings.sample_rate,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        observers=observers,
    )

    # A WorkerRunner attaches itself to the task; function-call handling in
    # pipecat 1.12 requires that attachment (a bare task.run() breaks tools).
    runner = WorkerRunner()
    await runner.add_workers(task)

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        async def _speak_greeting():
            await pipeline_started.wait()
            # Context already carries the greeting as an assistant message;
            # speak it without appending again.
            await task.queue_frames(
                [TTSSpeakFrame(text=tenant.greeting, append_to_context=False)]
            )

        task.create_task(_speak_greeting())

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        session.ended_reason = session.ended_reason or "client_disconnected"
        await task.cancel()

    try:
        await runner.run()  # returns when this call's pipeline finishes
    finally:
        ledger.call_ended(session.ended_reason or "pipeline_end")
        logger.info("Call end: {} ({})", session.call_id, session.ended_reason or "pipeline_end")
