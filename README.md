
# AI Voice Agent Platform

A real-time, multi-tenant AI voice agent platform built with **Python, Pipecat, and FastAPI**, designed to handle intelligent telephony conversations through Indian and international carrier integrations.

The platform combines streaming Speech-to-Text (STT), Large Language Models (LLMs), Text-to-Speech (TTS), intelligent turn detection, and agentic tool execution into a configurable voice automation pipeline.

It supports **Exotel (India)** and **Telnyx (International)** telephony architectures, with mock-provider support for running and testing the complete pipeline without external API credentials.

---

## Key Features

### 1. Real-Time Voice Processing

- Streaming speech processing pipeline using Pipecat.
- Speech-to-Text using Deepgram Nova-3.
- LLM-powered conversational intelligence using Google Gemini.
- Text-to-Speech generation using Cartesia.
- Configurable multilingual processing.
- Hindi-English code-switching configuration.

### 2. Intelligent Conversation Management

- Voice Activity Detection using Silero VAD.
- Smart Turn v3 for conversational turn prediction.
- Barge-in support for natural interruptions.
- Configurable endpointing and silence detection.
- Automated silence check-ins.
- Configurable call-duration limits.

### 3. Multi-Provider Telephony Architecture

- Exotel Media Streams integration for Indian telephony.
- Telnyx serializer architecture for international telephony.
- WebSocket-based audio streaming.
- Carrier-specific audio serialization and conversion.
- Phone-number-based tenant routing.

### 4. Agentic Tool Execution Engine

A configurable tool execution layer enables the voice agent to interact with external business systems.

Key capabilities include:

- Dynamic tool execution.
- Verified caller identity binding.
- HMAC-signed requests.
- Idempotency key management.
- Execution timeouts.
- Contextual filler responses while tools execute.
- Configurable tenant-specific tools.

### 5. Multi-Tenant Architecture

- Tenant-specific agent configurations.
- Phone-number-based tenant identification.
- Individual system prompts and voice settings.
- Tenant-specific tool definitions.
- Company knowledge base support.
- Isolated conversation and call configurations.

### 6. Company Knowledge Base

The platform supports tenant-specific company knowledge through Markdown and text documents.

- Automatic knowledge-base discovery.
- Keyword-based document retrieval.
- Gemini-powered contextual responses.
- Tenant-specific knowledge configuration.
- Dynamic document updates without restarting the server.

### 7. Observability & Monitoring

Each call generates a structured JSONL ledger containing:

- Conversation transcripts.
- Tool execution records.
- Time-to-first-byte (TTFB) metrics.
- Usage information.
- Smart Turn metrics.
- Per-call execution details.

---

## System Architecture

The platform follows a modular, streaming voice-processing architecture.

```text
                  TELEPHONY PROVIDERS
                  Exotel / Telnyx
                         |
                         v
                  WebSocket Transport
                         |
                         v
                  Audio Serialization
                         |
                         v
                 PIPECAT PIPELINE
                         |
          +--------------+--------------+
          |              |              |
          v              v              v
       STT Engine     Turn Detection   Audio Input
       Deepgram       Silero VAD
       Nova-3         Smart Turn v3
          |              |
          +--------------+
                  |
                  v
              LLM ENGINE
             Google Gemini
                  |
          +-------+-------+
          |               |
          v               v
      Tool Engine     Conversation
      Execution       Management
          |               |
          v               v
      External APIs    Tenant Context
          |               |
          +-------+-------+
                  |
                  v
              TTS ENGINE
                Cartesia
                  |
                  v
             Audio Streaming
                  |
                  v
             Caller Response
```

---

## Technology Stack

| Component | Technology |
|---|---|
| Programming Language | Python 3.11 |
| Voice Pipeline | Pipecat |
| Backend Framework | FastAPI |
| Speech-to-Text | Deepgram Nova-3 |
| LLM | Google Gemini |
| Text-to-Speech | Cartesia |
| Turn Detection | Silero VAD, Smart Turn v3 |
| Telephony | Exotel, Telnyx |
| Communication | WebSockets |
| Security | HMAC, Idempotency Keys |
| Testing | Pytest |
| Observability | JSONL Call Ledger |

---

## Project Structure

```text
.
├── apps/
│   └── server.py
│
├── va/
│   ├── bot.py
│   ├── config/
│   ├── telephony/
│   ├── pipeline/
│   ├── tools/
│   ├── mocks/
│   └── observability/
│
├── config/
│   └── tenants/
│       └── knowledge/
│
├── scripts/
│   ├── simulate_carrier.py
│   ├── talk_to_agent.py
│   ├── benchmark.py
│   └── make_fixtures.py
│
├── fixtures/
│   └── audio/
│
├── tests/
│
├── reports/
│
├── .env.example
├── pyproject.toml
└── README.md
```

---

## Getting Started

### Prerequisites

- Python 3.11
- Git
- Windows, macOS, or Linux

### 1. Clone the Repository

```bash
git clone <YOUR_REPOSITORY_URL>
cd <YOUR_PROJECT_DIRECTORY>
```

### 2. Create a Virtual Environment

**Windows:**

```bash
py -3.11 -m venv .venv
.venv\Scripts\activate
```

**macOS / Linux:**

```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -e ".[dev]"
```

### 4. Configure Environment Variables

Copy the example configuration:

```bash
copy .env.example .env
```

For real-provider execution, configure:

```env
VA_MOCK_MODE=false

DEEPGRAM_API_KEY=your_deepgram_api_key
GOOGLE_API_KEY=your_google_api_key
CARTESIA_API_KEY=your_cartesia_api_key

VA_TTS_VOICE_ID=your_cartesia_voice_id
```

Keep all credentials private. Never commit your actual `.env` file.

---

## Running the Application

### Mock Mode

The complete pipeline can run using mock STT, LLM, and TTS providers without external API credentials.

Start the server:

```bash
set VA_MODE=agent
python -m apps.server
```

### Run Automated Tests

```bash
python -m pytest tests -q
```

### Generate Audio Fixtures

```bash
python scripts/make_fixtures.py
```

### Simulate a Carrier Call

With the server running:

```bash
python scripts/simulate_carrier.py \
  --url "ws://localhost:8080/ws/exotel?did=+9111234567890" \
  --scenario en_order
```

### Run the Benchmark

```bash
python scripts/benchmark.py \
  --url "ws://localhost:8080/ws/exotel?did=+9111234567890"
```

### Talk to the Agent Using Your Microphone

With real providers configured:

```bash
python scripts/talk_to_agent.py
```

The microphone streams audio to the agent, and generated responses play through your speakers.

---

## Real Telephony Setup

The platform includes an Exotel Media Streams integration.

To connect a real Exotel number:

1. Configure your Exotel account and phone number.
2. Expose the FastAPI server through a public HTTPS/WSS tunnel or deployment.
3. Configure an Exotel call flow using the Media Streams WebSocket endpoint.
4. Map the phone number to the corresponding application.
5. Configure the appropriate tenant DID.
6. Test the audio pipeline using the real telephony connection.

Telnyx support is included in the carrier serializer architecture.

---

## Development & Validation

The project includes a mock-driven development and testing environment.

- Unit tests for serializers, tenant routing, and tool execution.
- Audio fixtures for repeatable speech scenarios.
- Carrier simulation for WebSocket protocol testing.
- Benchmarking infrastructure for pipeline evaluation.
- Structured call ledgers for debugging and performance analysis.

The real-provider pipeline has also been exercised with Deepgram Nova-3, Gemini 2.5 Flash, and Cartesia Sonic 3.6, including real Gemini function calling and streamed audio responses.

---

## Current Development Status

| Component | Status |
|---|---|
| Core FastAPI server | Implemented |
| Pipecat pipeline | Implemented |
| Mock STT / LLM / TTS | Implemented |
| Real STT / LLM / TTS integration | Tested |
| Agentic tool execution | Implemented and tested |
| Multi-tenant configuration | Implemented |
| Exotel simulator | Implemented |
| Real Exotel carrier validation | Pending |
| Telnyx carrier validation | Not yet established |
| Call transfer / DTMF / voicemail detection | Deferred |

---

## Security Considerations

- Store provider credentials in environment variables.
- Never expose API keys in source code.
- Validate caller identity before executing sensitive tools.
- Use HMAC signatures for external tool requests.
- Apply idempotency keys to prevent duplicate operations.
- Configure execution timeouts for external requests.
- Keep tenant-specific configurations isolated.

---

## Future Enhancements

- Production-grade carrier validation.
- Improved multilingual speech recognition.
- Embedding-based knowledge retrieval.
- Call transfer and DTMF support.
- Voicemail detection.
- WhatsApp and SMS extensions.
- Advanced call analytics and monitoring.
- Production multi-tenant hardening.

---

## Project Objective

The objective of this project is to build a modular, extensible voice automation platform capable of handling natural-language telephony interactions through real-time AI speech processing and agentic workflows.

By combining streaming audio, conversational intelligence, configurable tools, and multi-provider telephony support, the platform provides a foundation for building intelligent voice-based business automation systems.
