"""Runtime settings, loaded from environment / .env with VA_ prefix."""

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="VA_", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8080

    # 8000 for PSTN telephony; 16000 only for web/WebRTC channels
    sample_rate: int = 8000

    # "echo" (M1 bidirectional audio gate) or "agent" (full pipeline)
    mode: str = "agent"
    # True: scripted mock STT/LLM/TTS, zero API keys; False: real providers.
    # Each leg can be overridden independently (VA_MOCK_STT/LLM/TTS) so e.g.
    # real STT+TTS can be benchmarked while the LLM stays scripted.
    mock_mode: bool = True
    mock_stt: bool | None = None
    mock_llm: bool | None = None
    mock_tts: bool | None = None

    # "exotel" (default, India) or "telnyx" (international)
    carrier: str = "exotel"

    # STT (Deepgram) - vendor keys keep their standard unprefixed names
    deepgram_api_key: str = Field(
        default="", validation_alias=AliasChoices("DEEPGRAM_API_KEY", "deepgram_api_key")
    )
    stt_model: str = "nova-3"
    # raw string is passed through to Deepgram; "multi" = Hindi/English code-switch
    stt_language: str = "multi"

    # LLM (Google Gemini)
    google_api_key: str = Field(
        default="", validation_alias=AliasChoices("GOOGLE_API_KEY", "google_api_key")
    )
    llm_model: str = "gemini-2.5-flash"
    llm_temperature: float = 0.6
    # True: reach Gemini via Vertex AI express mode (aiplatform.googleapis.com)
    # with the same API key. Use when the Generative Language API is blocked
    # for the key's project (API_KEY_SERVICE_BLOCKED) - e.g. new-format AI
    # Studio keys until the API is enabled.
    llm_vertex: bool = False

    # TTS (Cartesia)
    cartesia_api_key: str = Field(
        default="", validation_alias=AliasChoices("CARTESIA_API_KEY", "cartesia_api_key")
    )
    tts_model: str = ""
    tts_voice_id: str = ""

    # Turn taking
    vad_stop_secs: float = 0.2
    min_start_words: int = 2

    # Tool engine
    filler_after_ms: int = 400
    tool_timeout_secs: float = 10.0
    mock_tool_delay_ms: int = 900
    tool_hmac_secret: str = "dev-secret"

    # Circuit breakers
    silence_timeout_secs: float = 45.0
    max_call_duration_secs: float = 900.0

    # Data
    tenants_dir: str = "config/tenants"
    data_dir: str = "data"

    @property
    def tenants_path(self) -> Path:
        p = Path(self.tenants_dir)
        return p if p.is_absolute() else PROJECT_ROOT / p

    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir)
        return p if p.is_absolute() else PROJECT_ROOT / p

    @property
    def use_mock_stt(self) -> bool:
        return self.mock_mode if self.mock_stt is None else self.mock_stt

    @property
    def use_mock_llm(self) -> bool:
        return self.mock_mode if self.mock_llm is None else self.mock_llm

    @property
    def use_mock_tts(self) -> bool:
        return self.mock_mode if self.mock_tts is None else self.mock_tts


def load_settings() -> Settings:
    return Settings()
