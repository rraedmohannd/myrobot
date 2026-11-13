"""Application settings, loaded from environment variables / .env (never hard-coded)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Smart Campus Robot Backend"
    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    # postgresql+asyncpg://user:password@host:5432/dbname
    database_url: str = Field(
        default="postgresql+asyncpg://robot:change-me@localhost:5433/smart_campus"
    )

    # Origins allowed to call the REST API (admin panel). Comma-separated in .env.
    cors_origins: str = "http://localhost:5173"

    # AI providers (selected through adapters; not used before phase B3/B4).
    llm_provider: Literal["openai", "anthropic"] = "openai"
    stt_provider: Literal["openai"] = "openai"
    tts_provider: Literal["openai"] = "openai"
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    # Model ids are configuration, not code: run `python -m app.ingestion.cli check-openai`
    # to see what the key can use, then set LLM_MODEL / EMBEDDING_MODEL in .env.
    llm_model: str = "gpt-6-luna"
    llm_max_output_tokens: int = 1500  # includes reasoning tokens on reasoning models
    # "low" since 2026-11-13: 10/10 on the bench questions and a faster first sentence
    # (docs/DECISIONS.md). Empty = not sent (the model's own default).
    llm_reasoning_effort: str | None = "low"
    # Speak the answer sentence by sentence while the model is still writing (demo sprint M2).
    # false = wait for the whole answer first (the behaviour before 2026-11-13).
    llm_streaming: bool = True
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536  # must match the vector(1536) column (migration 0003)

    # Speech (B4)
    stt_model: str = "gpt-transcribe"
    stt_prompt: str = (
        "محادثة بالعامية الأردنية أو بالإنجليزية مع روبوت استقبال كلية الهندسة والتصميم في "
        "جامعة الشرق الأوسط: هندسة الأنظمة الذكية، هندسة الطاقة المتجددة، هندسة العمارة، "
        "التصميم الداخلي، التصميم الجرافيكي، ساعات معتمدة، القبول والتسجيل."
    )
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "coral"
    tts_instructions: str = (
        "You are a friendly university receptionist robot. When the text is Arabic, speak it with "
        "a natural, warm Jordanian (Ammani, Levantine) accent and pronunciation, not formal "
        "Modern Standard Arabic. When the text is English, use a clear, friendly neutral accent. "
        "Speak at a lively, slightly fast, natural conversational pace, the way a friendly young "
        "Jordanian talks, with only short pauses between sentences. Cheerful and welcoming."
    )
    # Speaking pace (sprint 2). TTS_SPEED is the provider's own speed control (1.0 = normal):
    # measured on 2026-11-13, gpt-4o-mini-tts audio gets shorter with it (docs/DECISIONS.md).
    # SPEECH_RATE is a playback rate applied by the client (browser, later the Pi) with the pitch
    # preserved; keep it at 1.0 while TTS_SPEED does the work, or the two multiply.
    tts_speed: float = Field(default=1.12, ge=0.5, le=2.0)
    speech_rate: float = Field(default=1.0, ge=0.5, le=2.0)
    # The first spoken piece of an answer is cut after about this many words (at a comma when
    # there is one), so its audio is ready sooner; the rest is synthesized while it plays.
    tts_first_piece_words: int = Field(default=6, ge=3, le=20)
    # Send answer speech as PCM chunks while the provider is still generating it (clients that
    # ask for it in `hello`; docs/PROTOCOL.md). Measured 2026-11-13: first audio about 0.9 s
    # earlier than waiting for the whole mp3 of the first piece. false = whole mp3 pieces only.
    tts_streaming: bool = True
    # Silence that ends the visitor's utterance (client VAD), sent to the client in hello.ack.
    vad_silence_ms: int = Field(default=700, ge=300, le=2000)
    filler_enabled: bool = True  # short cached "لحظة بس…" right after a question (masks latency)
    follow_up_timeout_s: int = 10  # silence after "عندك سؤال ثاني؟" before a polite goodbye
    max_utterance_s: int = 20
    robot_token: SecretStr | None = None  # if set, /ws/robot requires ?token=...

    # Speakers (docs/SPEAKERS.md): anonymous, in-memory voiceprints per conversation
    speaker_model_path: str = "/models/speaker-campplus.onnx"
    speaker_threshold: float = 0.45  # cosine similarity: at or above = same voice
    speaker_new_threshold: float = 0.3  # below = a new visitor; in between = closest visitor
    speaker_min_seconds: float = 1.0  # shorter utterances use the face hint / last speaker
    max_speakers: int = 4

    # Retrieval-augmented answers
    rag_top_k: int = 8  # passages given to the LLM
    rag_candidates: int = 20  # candidates from each search before fusion
    # The best N hits of each search (keywords, meaning) always reach the LLM, even when the
    # other search did not find them (English question, Arabic passage). 0 = fusion only.
    rag_keep_top: int = 2
    chat_history_turns: int = 3  # previous turns of the same session sent as context

    # "Today" in the admin analytics is a day in this time zone (the database stores UTC).
    local_timezone: str = "Asia/Amman"

    # Token for the admin API/page (sent as X-Admin-Token). Empty = admin API disabled.
    admin_token: SecretStr | None = None

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
