# Voice Agent Platform: Corrected Build Plan

Pipecat-based, self-hosted, multi-tenant, resellable. It covers everything in the original plan: the pipeline, the tool engine, WhatsApp/SMS sync, backchanneling, sentiment, speculative execution, the four milestones, and the production config. The technical mistakes are fixed and the missing pieces are added.

**Verified against current docs:** Pipecat function-calling frames and API, Smart Turn v3 setup, the Telnyx and Exotel serializers, Deepgram Nova-3 multilingual Hindi, and the TRAI rules at a high level. **Not verified (check before you commit):** exact latency numbers, Cartesia Hindi voice quality, Telnyx India number availability, and TRAI details where sources conflict. Pin Pipecat to one version, because its API changes fast.

## Build-time addendum (verified against installed pipecat-ai 1.12.0, October 2026)

This addendum records what was verified and corrected while building the M0-M2 codebase in this repository.

| Item | Status in plan | Verified reality (pipecat-ai 1.12.0, Python 3.11) |
| --- | --- | --- |
| Pin | "pin one version" | Pinned `pipecat-ai==1.12.0` in `pyproject.toml`; requires Python >=3.11 (use 3.11: stdlib `audioop` is used and removed in 3.13+) |
| Exotel audio | "PSTN audio is 8kHz mu-law" | **Exotel Media Streams carry base64 PCM16 LE @ 8 kHz, NOT mu-law** (per the installed `ExotelFrameSerializer`). Telnyx is PCMU (mu-law). Both serializers resample internally; run the pipeline at 8000 Hz |
| Extras | silero/smart-turn extras | Silero VAD + Smart Turn v3 ONNX models are bundled in the wheel; `onnxruntime` is a base dependency. No `silero`/`openai`/`cartesia`/`telnyx` extras exist |
| Transport | websocket server transport | `FastAPIWebsocketTransport` (`pipecat.transports.websocket.fastapi`); the old `WebsocketServerTransport` is deprecated. One transport per WS connection; accept the socket, read the carrier `start` event yourself, then build the serializer with `stream_sid` |
| Execution | PipelineTask | Run each call through a **`WorkerRunner`** (`add_workers(task)` + `await runner.run()`). A bare `PipelineTask.run()` breaks tool calls ("runner is not set; call attach() first") |
| Greeting timing | - | Queue the greeting **after the pipeline has started** (e.g. via an observer's `on_pipeline_started`), not directly in `on_client_connected`; otherwise the caller's audio interrupts a half-started pipeline and startup deadlocks until the start timeout |
| Frames | FunctionCallsStartedFrame etc. | Confirmed. Note `EndTaskFrame`/`CancelTaskFrame` are deprecated since 1.4 -> use `EndWorkerFrame`. Queue filler via `await params.pipeline_worker.queue_frame(...)` |
| LLM settings | - | Every service needs a complete Settings object (`validate_complete()` on start). Deepgram: `DeepgramSTTSettings(model="nova-3", language="multi", ...)` - `language` accepts a raw string and is passed through, so `multi` reaches the API (API-side behavior still to verify with real keys) |
| Turn strategies | as planned | `UserTurnStrategies(start=[VADUserTurnStartStrategy, TranscriptionUserTurnStartStrategy, MinWordsUserTurnStartStrategy(min_words=N)], stop=[TurnAnalyzerUserTurnStopStrategy(LocalSmartTurnAnalyzerV3())])` via `LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2)))`. MinWords: 1 word starts a turn while the bot is silent, N words to barge in |
| Tools schema | ToolsSchema(standard=...) | `ToolsSchema(standard_tools=[...])`; tools can also be passed to `LLMContext(tools=...)` as `FunctionSchema` list (or `NOT_GIVEN` for no tools) |
| Metrics | per-turn logging | `PipelineParams(enable_metrics=True, enable_usage_metrics=True)` + a `BaseObserver` (`observe_every_push=False`) writing `MetricsFrame` data (TTFB/usage/TurnMetricsData incl. Smart Turn probability) to a JSONL ledger per call |

M0/M1/M2 status after this build: repo scaffold + echo bot (M1 gate passes via the carrier simulator) + full pipeline with Silero/Smart Turn/min-words + tenant tool engine (400ms filler, caller_id binding, HMAC, idempotency, structured errors) + benchmark harness (2 scenarios PASS in mock mode: en_order, hinglish_mixed). Real-carrier and real-provider runs remain user-side gates.

## 0. Corrections to the original plan

| Original | Problem | Fix |
| --- | --- | --- |
| Gateway "PCM 16kHz" | PSTN audio is 8kHz μ-law; Telnyx/Exotel send 8kHz, so upsampling adds no accuracy | Run the phone pipeline at 8kHz; the serializer converts μ-law ↔ PCM |
| Config "PCMU 8kHz OR Opus 16kHz" | Opus doesn't exist on the PSTN leg | PCMU/PCM 8kHz for phone; 16kHz only for a WebRTC/web demo channel |
| VAD as a separate node before STT | VAD now lives on the user aggregator/turn strategies | Configure VAD plus Smart Turn there |
| VAD 0.5 / 300ms alone | Cuts users off or lags | Silero VAD `stop_secs=0.2` plus Smart Turn v3 |
| `FunctionCallFrame`, `FunctionCallResultFrame`, `InterimTranscriptFrame` | Names wrong or inexact | Real ones: `FunctionCallsStartedFrame`, `FunctionCallInProgressFrame`, `FunctionCallResultFrame`, `InterimTranscriptionFrame` |
| Custom ToolRegistry engine that "pauses TTS" | Pipecat already does async function calling | Thin per-tenant wrapper over `FunctionSchema`/`ToolsSchema`/`register_function` |
| STT <150 + LLM <250 + TTS <100 = <500ms | Best case only; ignores endpointing and network | Target p50 ≤ ~1.0s, p95 ≤ ~1.6s (my estimates) |
| "<600ms total" in M4 | Unrealistic | Use the budget in section 6 |
| "Connection recovery for network drops" | A dropped PSTN call can't be resumed | Retry/fallback on STT/LLM/TTS providers; apologize and hang up or transfer; schedule a callback |
| DeepSeek V3 as default LLM | Latency variance, data-residency risk for client calls | Benchmark Gemini Flash-class, GPT mini-class and a fast-inference host on your scripts |
| Pitch/decibel sentiment | Unreliable on compressed 8kHz audio | Text sentiment plus behavioral signals (section 5) |
| Speculative LLM + pre-synthesized TTS, <150ms | Wasteful and unrealistic | Speculative **prefetch of read-only tools** only (section 5) |
| Multiplexing calls on 1 process | Python GIL & asyncio CPU limits choke on 20+ streams | Strictly 1 process/container per active call, scaled horizontally |
| Assuming no PSTN echo | PSTN leaks audio; bot hears itself and triggers false VAD interruptions | Server-side AEC / frequency filtering or WebRTC media bridge |

## 1. Architecture

```

PSTN → Carrier (Exotel for India / Telnyx intl) → WebSocket (8kHz μ-law)
→ Serializer (Exotel/Telnyx) → PCM
→ [transport.input] → [STT: Deepgram Nova-3, language=multi]
→ [user aggregator: Silero VAD + Smart Turn v3 + turn strategies]
→ [LLM] → [tool router] → [TTS: Cartesia/Sarvam/Azure Indic]
→ [transport.output] → [assistant aggregator]
Side channels: observers/metrics → logs, Redis queue → workers (WhatsApp/SMS, webhooks, post-call)

```

Components:

- **Telephony:** Exotel for India (it has a Pipecat serializer and Indian number series). Telnyx for international. Check Telnyx India number availability before relying on it.
- **STT:** Deepgram Nova-3 with `language=multi` supports Hindi/English code-switching. Hindi may come back in Latin-script Hinglish, so decide your transcript script policy early. Benchmark Sarvam as an Indic alternative.
- **LLM:** Provider-agnostic via Pipecat services. Choose by measured time-to-first-token on your scripts.
- **TTS:** Test Cartesia, Sarvam, Azure Indic and Rime Hindi voices on real scripts.
- **Hosting & Scaling:** Pipecat Cloud/LiveKit Cloud, or self-host with Docker. Put compute **in the same region as the carrier and your STT/TTS endpoints** (Mumbai for India). **Concurrency topology:** Python `asyncio` runs on a single thread—do not multiplex dozens of pipelines in a single process. Run **1 worker process/container per active call**, scaling horizontally via Kubernetes, Docker Swarm, or a Redis-backed queue.

## 2. Core pipeline (verified imports; assemble per your pinned version's docs)

```python
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
from pipecat.serializers.telnyx import TelnyxFrameSerializer   # or the Exotel serializer
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import FunctionCallParams

vad = SileroVADAnalyzer(params=VADParams(stop_secs=0.2))  # Smart Turn requires 0.2
stop = TurnAnalyzerUserTurnStopStrategy(turn_analyzer=LocalSmartTurnAnalyzerV3())
# Pass VAD + strategies via LLMUserAggregatorParams / UserTurnStrategies
# (see Pipecat "user turn strategies" docs for your version).
# Order: input → stt → user_agg → llm → tts → output → assistant_agg

```

Rules:

* Set the pipeline audio in/out sample rate to 8000 for telephony.
* Turn start: VAD plus transcription (the default). Add a **minimum-words-to-interrupt** strategy to ignore coughs and line noise.
* **Echo Management:** PSTN lines leak audio. Use server-side Acoustic Echo Cancellation (AEC) or filter outbound TTS frequencies to prevent the bot from self-interrupting.
* **Circuit Breakers:**
* Auto-hangup on >45 seconds of continuous VAD silence ("Are you still there?").
* Hard cap absolute call duration at 15 minutes to prevent infinite billing loops from voicemails or desk drops.

* Add noise suppression (Krisp VIVA filter, which is licensed, or RNNoise). Phone audio is noisy.
* The serializer can hang up the call on `EndFrame`/`CancelFrame` when you give it carrier credentials.

## 3. Tool engine (multi-tenant, zero-refactor)

Use Pipecat's built-in function calling. Add only a thin per-tenant layer:

1. Each tenant config holds tool definitions: JSON schema, endpoint URL, auth, timeout, `read_only` flag.
2. At call start, build a `ToolsSchema` from the tenant config, then `llm.register_function(name, handler)` for each tool. `tools=[]` is valid on day 1.
3. The handler calls the tenant endpoint with a hard timeout and returns the result through `params.result_callback`. On error or timeout it returns a structured error so the LLM can apologize and offer a human.
4. **Filler at 400ms:** start the HTTP call as a task and wait on it for 400ms (`asyncio.wait_for(asyncio.shield(task), 0.4)`). If it times out, push a short filler (`TTSSpeakFrame` or a pre-rendered clip), then keep awaiting. Cancel the filler path if the result arrives first.
5. **Security (Contextual Data Binding):** Never allow the LLM to supply user-sensitive parameters (e.g. `phone_number` for payments or CRM lookups). Force tool handlers to inherit the verified `caller_id` directly from the SIP header, overriding LLM arguments to prevent voice prompt injection attacks.
6. Validate arguments against the schema and allowlist tenant URLs. Sign outgoing requests (HMAC). Never let the LLM choose a URL.
7. Make write tools idempotent, using a call-ID + tool-call-ID key.

## 4. Telephony and compliance (India; verify with your carrier or a lawyer)

* Inbound calls are far simpler. Start with inbound or consented service calls.
* Outbound AI calls fall under TRAI's TCCCPR like human calls:
* DLT registration of the Principal Entity, and registered voice headers/templates.
* Correct number series: 140 for promotional, a separate registered series for service/transactional. Sources conflict on the exact series and the permitted calling hours, so confirm with your carrier.
* Dial-time DND scrubbing.
* Recorded explicit consent for promotional calls.
* AI disclosure at the start of the call.
* Auditable logs.

* Call recordings need a consent announcement, and recordings and transcripts need DPDP-aware retention.
* For the US/EU, check consent rules for AI-voice calls (TCPA, GDPR) before launching there.

## 5. Phase 2/3 extensions (corrected; build only after Milestone 5 metrics justify them)

**A. WhatsApp/SMS sync.** Tool returns to the LLM immediately; the handler enqueues a job (Redis + RQ/Celery/arq) with an idempotency key. A worker sends via a WhatsApp Business BSP using **pre-approved templates** (and SMS via a DLT-registered header/template in India). Never block the audio loop.

**B. Backchanneling ("mm-hmm").** Implement as an inline `FrameProcessor` (observers are read-only), not a bypass. Track user-speech duration from VAD start/stop frames. Inject a pre-rendered 8kHz clip only when the bot is silent, the user has spoken more than ~4s continuously, and at most once per ~8s. A/B test it, because it can interfere with turn-taking and echo. Default off.

**C. Sentiment.** Don't use pitch/dB alone. Combine LLM text sentiment per turn with behavioral signals: interruption count, repeated requests, speech-rate change, long silences. Store `session.user_sentiment`. On a change, append a system message to the LLM context (e.g. `LLMMessagesAppendFrame`). Use the same signal to trigger human handoff.

**D. Speculative execution.** On `InterimTranscriptionFrame`, run a cheap intent classifier. If confidence is high, **prefetch read-only tool data** (CRM lookup, order status) and cache it for this turn; discard on mismatch. Don't speculate on LLM generation or pre-synthesize audio (cost and cancellation issues). Real latency wins come from streaming sentence-by-sentence TTS, prompt caching, a short system prompt, and a fast LLM.

## 6. Latency budget (estimates; measure per turn)

| Stage | Typical |
| --- | --- |
| Carrier + network (both ways) | 100-250ms |
| Endpointing (VAD 0.2s + Smart Turn) | 200-350ms |
| STT finalization | 100-250ms |
| LLM time to first token | 300-700ms |
| TTS time to first audio | 100-250ms |
| **Total** | **~0.8-1.5s** |

Targets: p50 ≤ 1.0s, p95 ≤ 1.6s. Interruption: bot audio stops ≤ ~300ms after the user starts speaking.

## 7. Milestones (single strong dev; estimates)

| # | Weeks | Deliverables | Exit criteria |
| --- | --- | --- | --- |
| M0 | 0-1 | Pick country/vertical/direction. Carrier account and compliance path. Benchmark harness: 20 scripted calls, 2-3 STT/LLM/TTS candidates, Hindi/English/Hinglish. **Parallel:** a Vapi/Retell/Bolna pilot to find paying clients. | Chosen stack with measured latency and WER; compliance route confirmed |
| M1 | 1-2 | Carrier → WebSocket → serializer → echo bot. HTTPS/WSS behind a proxy. Region-matched hosting. | Real call connects; bidirectional audio clean for 5 minutes |
| M2 | 2-4 | STT + LLM + TTS + Smart Turn + noise filter + min-words interruption. Per-turn latency logging. | Latency targets met on 20 test calls; interrupt ≤ 300ms; no cut-offs on pauses |
| M3 | 4-5 | Tenant-config tool layer, filler at 400ms, idempotent writes, error paths, call transfer (SIP/REFER or carrier API), DTMF, voicemail detection (outbound). Contextual binding for tool security. | Mock and real tool calls work; slow/failing tool handled gracefully |
| M4 | 5-7 | DID → tenant router, per-tenant prompt/voice/tools, per-call usage ledger (duration, per-provider cost), recordings/transcripts, post-call summary, provider fallbacks, load test, autoscale on concurrent calls (1 process/call), graceful deploy drain, circuit breakers (silence/duration caps), alerts. | Multiple numbers run distinct agents concurrently; load test at target concurrency passes; ledger reconciles with provider bills |
| M5 | 7-10 | Reseller layer: client dashboard, sub-accounts, white-label branding/domain, billing (Razorpay/Stripe), prompt/flow editor, CRM/calendar integrations, templates per vertical. | First paying client onboarded without dev help |
| P2 | after M5 | WhatsApp/SMS sync (5A) | Message sent within seconds with zero audio impact |
| P3 | after P2 | Backchannel, sentiment, speculative prefetch (5B-D), each behind a flag and A/B tested | Each shows a measured gain in completion rate or latency, or is removed |

## 8. Production config

| Setting | Value |
| --- | --- |
| Carrier audio | 8kHz μ-law (PCMU); serializer converts to PCM |
| Pipeline rate | 8000 Hz for phone; 16000 Hz only for web/WebRTC |
| VAD | Silero, `stop_secs=0.2` |
| Turn end | Smart Turn v3 |
| Turn start | VAD + transcription, with a minimum-words threshold |
| Echo Management | Strict server-side AEC required for PSTN, or bridge via WebRTC media server to prevent self-interruption |
| STT | Deepgram Nova-3, `language=multi` (confirm Hindi script output) |
| Noise | Krisp VIVA or RNNoise |
| Concurrency Architecture | Strictly 1 Process/Container per active call. Horizontally scale via K8s/Docker Swarm or Pipecat Cloud instances. Do not multiplex 50 calls in one Python process |
| Security (Tool Params) | Force CRM/DB tools to inherit verified `caller_id` directly from SIP headers; never let LLM dictate target phone numbers |
| Circuit Breakers | Auto-hangup on >45s of VAD silence; Hard cap call duration at 15 minutes |
| Process model | One bot process/task per call; warm pool; autoscale on concurrent calls |
| Observability | Per-turn STT/LLM/TTS timings, call logs, transcripts, error alerts |
| Testing | Scripted test calls on every deploy; regression set in Hindi/English/Hinglish |
| Security | Tenant isolation, HMAC-signed tool calls, secrets in a vault, recording retention policy |

## 9. Risks

1. No customers yet → run the managed-platform pilot in parallel with M0-M2.
2. Compliance can block outbound → start inbound.
3. Indic accuracy → test on real call audio before committing to vendors.
4. Per-call cost drift → log per-provider cost per call from day 1.
5. Pipecat version churn → pin versions and re-test on upgrades.
6. Scaling bottleneck & PSTN echo → enforce 1 process per call containerization and server-side AEC from day 1.
