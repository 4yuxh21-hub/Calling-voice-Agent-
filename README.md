# Voice Agent Platform

Pipecat-based multi-tenant voice agent for telephony (Exotel India first, Telnyx international). Runs the **full pipeline in mock mode with zero API keys**; add keys to switch to real providers.

- STT: Deepgram Nova-3 (`language=multi` for Hindi/English code-switching)
- LLM: Google Gemini (configurable)
- TTS: Cartesia (Hindi-capable voices; verify quality on real scripts)
- Turn detection: Silero VAD (`stop_secs=0.2`) + Smart Turn v3 (bundled) + min-words barge-in gate
- Tool engine: per-tenant tools with 400ms filler, verified caller-id binding, HMAC signing, idempotency keys, hard timeouts
- Guards: >45s silence check-in + 15-minute call cap
- Observability: per-call JSONL ledger (transcripts, tool calls, TTFB/usage/Smart-Turn metrics)

See [PLAN.md](PLAN.md) for the full build plan and the verified-Pipecat-API addendum.

## Requirements

- Python **3.11** (3.13+ removed stdlib `audioop` which Pipecat's audio path uses)
- Windows/macOS/Linux; no special audio deps needed for telephony-only use

## Quickstart (zero keys, mock mode)

```bat
py -3.11 -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
copy .env.example .env

:: 1) unit tests
.venv\Scripts\python -m pytest tests -q

:: 2) generate speech fixtures (uses Windows SAPI TTS; ~4 small 8kHz WAVs)
.venv\Scripts\python scripts\make_fixtures.py

:: 3) M1 echo gate: terminal 1
set VA_MODE=echo&& .venv\Scripts\python -m apps.server
::    terminal 2 (audio loops back through the full serializer path)
.venv\Scripts\python scripts\simulate_carrier.py --audio fixtures\audio\en_bye.wav

:: 4) full agent pipeline (mock STT/LLM/TTS, real tool engine + Smart Turn): terminal 1
set VA_MODE=agent&& .venv\Scripts\python -m apps.server
::    terminal 2
.venv\Scripts\python scripts\simulate_carrier.py --url "ws://localhost:8080/ws/exotel?did=+9111234567890" --scenario en_order

:: 5) benchmark (server running in agent mode)
.venv\Scripts\python scripts\benchmark.py --url "ws://localhost:8080/ws/exotel?did=+9111234567890"
```

## Talk to your agent with your own voice (softphone)

With the server running in agent mode (real providers are configured in `.env`):

```bat
.venv\Scripts\python scripts\talk_to_agent.py
```

Your microphone streams to the agent and its replies play through your speakers, with working barge-in (start talking over it). `--save-reply bot.wav` records the agent's side; `--duration 60` auto-hangups. **Wear headphones** — without echo cancellation the mic hears the agent through the speakers and it interrupts itself (the PSTN echo problem from PLAN.md). To watch what the agent hears/says live, tail the ledger it prints: `powershell -Command "Get-Content data\ledger\<call_sid>.jsonl -Wait"`.

The simulator speaks the Exotel Media Streams WebSocket protocol (base64 PCM16 @ 8kHz `media` events), replays scenario audio, and prints a JSON summary. Benchmark reports land in `reports/`; per-call ledgers in `data/ledger/<call_sid>.jsonl`.

## Real providers

1. Copy `.env.example` → `.env`, set `VA_MOCK_MODE=false` and the API keys (`DEEPGRAM_API_KEY`, `GOOGLE_API_KEY`, `CARTESIA_API_KEY`).
2. Set a Cartesia voice id (`VA_TTS_VOICE_ID`) from their voices gallery — verify Hindi voice quality on real scripts before committing.
3. Re-run the benchmark: now real STT/LLM/TTS handle the same scenarios (the M0/M2 exit criteria).

Each service leg can also be mocked independently while the others run for real:

```ini
VA_MOCK_MODE=false
VA_MOCK_STT=false   # real Deepgram
VA_MOCK_LLM=true    # scripted replies (e.g. while a key is pending)
VA_MOCK_TTS=false   # real Cartesia
```

**Measured 2026-10-06 with the FULL real stack** — Deepgram nova-3/multi + Gemini 2.5 Flash + Cartesia sonic-3.6: the complete agentic loop works, including **real Gemini tool calling** (it asks for the order number, extracts it from what it heard, calls `get_order_status`, and uses the tool result in its reply), the 400ms filler, and streaming Cartesia audio.

**Google key note:** new-format AI Studio keys (`AQ.Ab8R...`) hit `API_KEY_SERVICE_BLOCKED` on the Generative Language API for their auto-created project. Fix options: (a) enable the "Generative Language API" for that project in Cloud Console, or (b) set `VA_LLM_VERTEX=true` — the same key then reaches Gemini through Vertex AI express mode (currently configured and verified working).

Known M2 tuning items surfaced by the benchmark (not bugs):
- First-utterance finals from Deepgram are unreliable right after the greeting (interims arrive, finals often don't) — this is the plan's "no cut-offs on pauses" M2 exit criterion and needs endpointing/turn tuning.
- The benchmark's `expected_tools_called` / `reply_keywords` gates assume the scripted mock LLM. A real LLM negotiates (clarifying questions, rephrasing), so judge those checks by reading the ledger transcripts, not the PASS flag.
- The Hinglish fixture is an English TTS voice reading Latin-script Hindi — not representative; real Indic call audio is the plan's stated next test.
- Benchmark WER is **reported, not gated**: through the live pipeline, barge-in and early turn-ends fragment finals, so a hard WER gate measures turn-taking, not STT. For clean provider WER, score the fixture WAVs offline against the pre-recorded API.

Gotchas encoded in the code (learned the hard way):
- Vendor keys keep their standard unprefixed names in `.env` (`DEEPGRAM_API_KEY`, …) — pydantic-settings' `VA_` prefix otherwise hides them and services connect with empty keys (401).
- Cartesia's API needs a dated `Cartesia-Version` header (pinned to `2026-08-14` in `va/pipeline/services.py`).

## Company knowledge base (make the agent answer about YOUR company)

Per-tenant knowledge lives in `config/tenants/knowledge/<tenant_id>/` as `.md`/`.txt` files (the demo tenant ships one at `config/tenants/knowledge/demo/company.md`). Any tenant with files there automatically gets a `search_company_kb` tool: Gemini searches the files whenever a caller asks something company-specific and answers from the retrieved text.

- **Format**: one `## Heading` per topic, short paragraphs. Files are re-read on every call — edit them while the server runs and the next call picks it up, no restart.
- **Test it**: `python scripts\simulate_carrier.py --scenario kb_demo` (or just ask the agent "What is your return policy?" over the softphone).
- **Small info** (hours, address) can alternatively go straight into the tenant's `system_prompt` in `config/tenants/demo.json`.
- **Limits**: retrieval is keyword-based (RAG-lite) — fine for hundreds of KB; if callers paraphrase heavily ("money back" vs "refund"), include synonyms in the docs. The upgrade path is embedding-based retrieval behind the same tool interface (deferred to M5/P2 per plan).
- **Tenant-level facts** the agent must never invent: the demo tenant's prompt instructs "answer only from what the KB returns — never invent company details."

## Real carrier (Exotel)

Account status anytime: `python scripts\exotel_status.py` (reads `EXOTEL_*` from `.env`).

1. **Rent a number** in the Exotel dashboard (Numbers → rent). Fresh trial accounts need KYC (entity details) for Indian numbers. Trial also restricts the v2 apps API — build the call flow in the visual editor instead.
2. **Expose the bot publicly**: install [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/), run `cloudflared tunnel --url http://localhost:8080`, and note the `https://<random>.trycloudflare.com` URL (carriers require WSS).
3. **Build the call flow** (App BXML / Build app visual editor): incoming call → **Stream** widget with URL `wss://<tunnel-host>/ws/exotel?did=<your-exotel-number>`. The `did` query parameter is what routes the call to a tenant (the applet's `customParameters` also work — `did`, `caller`, and the benchmark's `script` key are honored).
4. **Map the number to that app** in the number's settings.
5. **Call it from your phone** — first with `VA_MODE=echo` (you hear yourself back = the M1 gate on a real carrier), then `VA_MODE=agent` to talk to the real agent.

Tenant routing: `config/tenants/*.json` maps DIDs → prompts/voice/tools. The demo tenant's tools are built-in mocks (no external endpoint needed). Point a tool at a real endpoint by setting `url` in the tenant config; requests are HMAC-signed (`VA_TOOL_HMAC_SECRET`) and carry an idempotency key.

## Project layout

```
apps/server.py            FastAPI entrypoint (one pipeline per WS connection)
va/bot.py                 per-call assembly: transport -> pipeline -> WorkerRunner
va/config/                settings (env) + tenant loading/DID routing
va/telephony/             serializer factory (Exotel/Telnyx) + WS routes
va/pipeline/              guards (silence/duration caps), echo processor, service builder
va/tools/                 tool engine (filler/binding), HTTP executor, mock tools
va/mocks/                 mock STT/LLM/TTS + benchmark scenario loading
va/observability/         metrics observer + JSONL call ledger
scripts/                  carrier simulator, fixture generator, benchmark harness
fixtures/                 8kHz speech WAVs + scenario definitions
tests/                    serializer round-trips, tenants, tool engine units
```

## Notes & gotchas (learned during the build, see PLAN.md addendum)

- Exotel Media Streams audio is **base64 PCM16 @ 8kHz** (not μ-law); Telnyx is PCMU. The serializers handle conversion; keep the pipeline at 8000 Hz.
- Each call runs through its own `WorkerRunner`; running a bare `PipelineTask` breaks function calling in Pipecat 1.12.
- The greeting is queued after the pipeline reports started; queuing it on socket-connect races the caller's audio and can deadlock startup.
- Mock STT finalizes utterances after ~650ms of silence; scenario WAVs are single utterances (Windows SAPI voices; Hindi texts are Latin-script Hinglish, so the default English SAPI voice reads them — fine for timing tests, not an accent reference).
- Mock tool delay (`VA_MOCK_TOOL_DELAY_MS=900`) exceeds the filler threshold (`VA_FILLER_AFTER_MS=400`) so the filler path is exercised in every demo; set it to 0 to disable.
- Deepgram `language=multi` is passed through as a raw string; verify code-switching behavior on real calls when keys are added.

## Milestone status

| Milestone | Status |
| --- | --- |
| M0 harness | Benchmark harness + fixtures + scenarios ready; real-provider numbers pending keys |
| M1 echo gate | Passes via carrier simulator; real-carrier call pending Exotel account |
| M2 pipeline | Full pipeline runs end-to-end in mock mode with latency/metrics logging; real-provider latency/WER pending |
| M3 tools | Filler/binding/HMAC/timeout/idempotency implemented and unit-tested; call transfer, DTMF, voicemail detection deferred |
| M4+ | Multi-tenant prod hardening, reseller layer, WhatsApp/SMS, P2/P3 extensions: deferred per plan |
#   C a l l i n g - v o i c e - A g e n t -  
 #   C a l l i n g - v o i c e - A g e n t -  
 