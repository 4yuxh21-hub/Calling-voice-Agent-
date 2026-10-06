"""Real service construction: Deepgram STT, Gemini LLM, Cartesia TTS.

Built per service (not as a set) so any leg can be mocked independently via
VA_MOCK_STT / VA_MOCK_LLM / VA_MOCK_TTS while the others run for real.
"""

from loguru import logger
from google import genai
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.deepgram.stt import DeepgramSTTSettings as DGSettings
from pipecat.services.google.llm import GoogleLLMService
from pipecat.services.google.llm import GoogleLLMSettings as GSettings

from va.config.settings import Settings
from va.config.tenants import TenantConfig

# Cartesia's REST/websocket APIs require a dated version header.
CARTESIA_VERSION = "2026-08-14"


class GoogleVertexLLMService(GoogleLLMService):
    """GoogleLLMService speaking to Gemini via Vertex AI express mode.

    Same API key, different endpoint (aiplatform.googleapis.com). Needed when
    the Generative Language API is blocked for the key's project.
    """

    def create_client(self):
        self._client = genai.Client(
            vertexai=True, api_key=self._api_key, http_options=self._http_options
        )


def build_stt(settings: Settings):
    return DeepgramSTTService(
        api_key=settings.deepgram_api_key,
        settings=DGSettings(
            model=settings.stt_model,
            language=settings.stt_language,  # raw string; "multi" passes through
            interim_results=True,
            smart_format=True,
            # Turn-end latency: default endpointing (~500ms) is too slow for
            # phone calls; VAD (200ms) + Smart Turn carry the real decision,
            # so Deepgram just needs to finalize transcripts quickly.
            endpointing=200,
            utterance_end_ms=500,
        ),
    )


def build_llm(settings: Settings):
    cls = GoogleVertexLLMService if settings.llm_vertex else GoogleLLMService
    logger.info("LLM: {} via {} ({})", settings.llm_model,
                "Vertex express" if settings.llm_vertex else "Generative Language API",
                cls.__name__)
    return cls(
        api_key=settings.google_api_key,
        settings=GSettings(model=settings.llm_model, temperature=settings.llm_temperature),
    )


def build_tts(settings: Settings, tenant: TenantConfig):
    tts_kwargs = {}
    tts_model = tenant.voice.model or settings.tts_model
    if tts_model:
        tts_kwargs["model"] = tts_model
    voice_id = tenant.voice.voice_id or settings.tts_voice_id
    if voice_id:
        tts_kwargs["voice"] = voice_id
    # Cartesia needs a concrete language; "multi" is a Deepgram-only concept.
    tts_kwargs["language"] = "hi" if tenant.language.startswith("hi") else "en"
    tts = CartesiaTTSService(
        api_key=settings.cartesia_api_key,
        sample_rate=settings.sample_rate,
        cartesia_version=CARTESIA_VERSION,
        settings=CartesiaTTSService.Settings(**tts_kwargs),
    )
    logger.info(
        "Real services: Deepgram {}/{} + Gemini {} + Cartesia {} ({})",
        settings.stt_model, settings.stt_language, settings.llm_model,
        voice_id or "default-voice", tts_model or "sonic-3.6",
    )
    return tts
