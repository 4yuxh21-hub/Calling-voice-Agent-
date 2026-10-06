# Voice Agent Platform — First Build (Skeleton through M2)

## Scope
Build the Pipecat-based voice-agent codebase from scratch in the empty workspace, per the corrected build plan and your choices: **Exotel first** (Telnyx behind config), **Deepgram STT + Google Gemini LLM + Cartesia TTS** defaults (all swappable via .env), **English + Hindi/Hinglish**. Everything runs end-to-end in **mock mode with zero API keys**; real providers activate when keys are added. Deferred: call transfer, DTMF, voicemail detection (M3+), prod scaling/K8s (M4), reseller dashboard (M5), WhatsApp/SMS (P2), backchannel/sentiment/speculation (P3).

## Verified stack facts (researched today, build against these)
- Pin `pipecat-ai==1.12.0` (Python ≥3.11). Silero VAD + Smart Turn v3 ONNX models are **bundled in the wheel** — no silero/smart-turn extras; Windows-local dev works via onnxruntime.
- Telephony transport: `pipecat.transports.websocket.fastapi.FastAPIWebsocketTransport` (old `WebsocketServerTransport` is deprecated). Serializers confirmed: `pipecat.serializers.exotel.ExotelFrameSerializer`, `pipecat.serializers.telnyx.TelnyxFrameSerializer`.
- Turns: `UserTurnStrategies` with `MinWordsUserTurnStartStrategy` (start) and `TurnAnalyzerUserTurnStopStrategy(LocalSmartTurnAnalyzerV3())` (stop), wired via `LLMUserAggregatorParams` on `LLMContextAggregatorPair`. Execution: `PipelineWorker` + `WorkerRunner`, `PipelineParams(enable_metrics=True)`.
- Function calling: `FunctionSchema`/`ToolsSchema` + `llm.register_function`, handlers end with `await params.result_callback(...)`.
- Known build-time checks: (1) Deepgram `language="multi"` is not in Pipecat's typed `Language` enum — verify how to pass it in 1.12 (`DeepgramSTTService.Settings`), fallback `nova-3` + language config; (2) confirm `pipecat.services.google.llm.GoogleLLMService` import + current flash model id; (3) mirror the exact Exotel WS JSON event schema from the installed serializer source when writing the simulator.

## Repo layout
```
PLAN.md                  # your corrected build plan, saved + amended with verified 1.12.0 API facts
README.md                # setup/run/test; ngrok-cloudflared for carrier testing; Windows notes
pyproject.toml           # pipecat-ai[runner,websocket,deepgram,google]==1.12.0 (+dev: pytest, jiwer, httpx)
.env.example  .gitignore  Dockerfile  docker-compose.yml
va/                      # package
  config/settings.py     # pydantic-settings: BOT_MODE (echo|agent), carrier, providers, caps (silence 45s, duration 15m)
  config/tenants.py      # load config/tenants/*.json → TenantConfig; DID → tenant routing
  bot.py                 # per-connection bot factory: echo or full pipeline
  telephony/routes.py    # FastAPI webhook + WS routes (/exotel/..., /telnyx/...)
  telephony/serializers.py  # carrier → configured serializer factory
  pipeline/build.py      # transport → STT → user agg (Silero stop_secs=0.2, Smart Turn v3, min-words start) → LLM → TTS → out; 8kHz
  pipeline/guards.py     # FrameProcessor circuit breakers: >45s silence ("Are you still there?"), 15-min hard cap
  tools/engine.py        # tenant tools → ToolsSchema + handlers; 400ms filler (shielded task + TTSSpeakFrame); caller_id binding override
  tools/executor.py      # HTTP call w/ hard timeout, HMAC signing, URL allowlist, structured errors, idempotency key
  tools/mock_tools.py    # demo tools (order status / booking lookup) needing no external endpoint
  observability/metrics.py  # per-turn STT/LLM/TTS timings via metrics frames/observers → JSONL
  mocks/mock_services.py # MockSTT (scripted transcripts), MockLLM (canned replies + fake tool calls), MockTTS (synthetic audio)
apps/server.py           # FastAPI app: mounts routes, one pipeline task per WS connection
scripts/simulate_carrier.py  # WS client speaking Exotel media-stream protocol; replays fixture audio (μ-law 8kHz)
scripts/benchmark.py     # M0 harness: scenarios → per-turn latency, STT/LLM/TTS timings, WER (jiwer), tool-call correctness → report
config/tenants/demo.json # demo tenant: bilingual prompt, voice id placeholder, 2 mock tools
fixtures/audio/          # small en/hi/hinglish 8kHz μ-law samples; fixtures/scenarios/ scripted turns
tests/                   # serializer round-trip, tool engine (filler timing, caller_id override, HMAC), tenant config
```

## How the dev loop works without keys
`MOCK_MODE=true` + `BOT_MODE=echo` verifies bidirectional audio (M1 gate) using `simulate_carrier.py` against localhost. `MOCK_MODE=true` + `BOT_MODE=agent` runs the full turn pipeline with scripted transcripts/canned replies, exercising Smart Turn, min-words interruption, tool engine, guards, and metrics. Adding real keys switches providers per `.env` — no code changes. Carrier testing needs a public WSS (ngrok/cloudflared instructions in README); Exotel webhook/flow setup steps documented there too.

## Implementation order
1. Scaffold: pyproject (pinned), package tree, .env.example, .gitignore, git init, PLAN.md/README.
2. Telephony: serializer factory + FastAPI routes + echo bot + Exotel-protocol simulator (schema read from installed serializer source) — pytest round-trip tests.
3. Pipeline: settings/tenant config, full agent pipeline with Silero + Smart Turn v3 + min-words start strategy, mock STT/LLM/TTS, guards, metrics ledger.
4. Tool engine: schema build from tenant JSON, handlers with 400ms filler, caller_id binding, HMAC/allowlist/timeout, mock tools.
5. Benchmark harness + en/hi/hinglish fixtures; run full test suite; run mock end-to-end and attach the latency report.

## Verification before handoff
`pip install -e .` on this Windows machine (Python ≥3.11 confirmed), pytest green, echo-mode simulator round-trip passes, agent-mode mock run produces transcripts + per-turn latency JSONL + tool-call demo, benchmark report generated for the fixture scenarios. Real-carrier call, real-provider latency/WER numbers, and Deepgram multi-language behavior remain user-side gates (need accounts/keys) — flagged in README as the M0/M2 exit criteria.

## Prerequisites only you can provide (not blocking this build)
Exotel account + media-stream-enabled number; DEEPGRAM_API_KEY, GOOGLE_API_KEY, CARTESIA_API_KEY; later: DLT/TRAI compliance path for outbound (plan §4).