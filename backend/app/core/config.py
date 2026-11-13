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
    # ask for it in `hello`; docs/PROTOCOL.md). OFF by default: on 2026-11-13 the first 85 ms
    # arrived early (about 0.7 s) but the continuous audio only 0.5 s later, and the end-to-end
    # run showed no gain over whole mp3 pieces (docs/DECISIONS.md). true = try it.
    tts_streaming: bool = False
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

    # --- Robot control and LAN (sprint 3) ---
    # A robot is "offline" when no robot.status arrived for this long; an unanswered command is
    # reported as timed out after robot_command_timeout_s.
    robot_offline_after_s: float = 5.0
    robot_command_timeout_s: float = 5.0
    # Peers in these networks may connect to /ws/robot without ROBOT_TOKEN (comma-separated
    # CIDR). Default: this machine only. Under Docker Desktop the laptop's own browser does NOT
    # count as local (it arrives through Docker's network), see docs/DEPLOY.md.
    robot_local_nets: str = "127.0.0.1/32,::1/128"
    # Address other devices use to reach this backend, e.g. http://192.168.1.20:8000 (for the QR
    # on the admin page). Empty = the address the admin page itself was opened with.
    public_base_url: str = ""

    # --- FAQs (sprint 3): an approved FAQ answers before retrieval when the question matches ---
    # Cosine similarity of the question vectors. 0.88 from a small calibration on 2026-11-13:
    # reworded versions of the same question scored 0.90-0.99, different questions about the
    # same program at most 0.80 (docs/DECISIONS.md).
    faq_match_threshold: float = Field(default=0.88, ge=0.5, le=1.0)

    # --- Resilience (sprint 3): every provider call has a time limit, retries and a breaker ---
    provider_timeout_embedding_s: float = 6.0
    provider_timeout_stt_s: float = 12.0
    provider_timeout_llm_first_s: float = 10.0  # until the first text of a streamed answer
    provider_timeout_llm_s: float = 25.0  # whole answer
    provider_timeout_tts_s: float = 12.0
    provider_retries: int = Field(default=1, ge=0, le=3)  # extra attempts after a failure
    provider_retry_backoff_s: float = 0.4  # doubled on each further attempt
    breaker_failures: int = Field(default=3, ge=1)  # failures in a row that open the breaker
    breaker_open_s: float = 30.0  # how long calls fail at once before one trial call is allowed
    # Official faculty page shown as a QR when the internet is weak. Empty = the address of the
    # approved faculty page in the sources table.
    faculty_page_url: str = ""

    # --- Privacy and admin login (sprint 3) ---
    conversation_retention_days: int = Field(default=30, ge=1)
    admin_session_hours: int = Field(default=12, ge=1)

    # Token for the admin API/page (sent as X-Admin-Token). Empty = admin API disabled.
    admin_token: SecretStr | None = None

    @property
    def robot_local_net_list(self) -> list:  # list[IPv4Network | IPv6Network]
        import ipaddress

        nets = []
        for item in self.robot_local_nets.split(","):
            if item.strip():
                try:
                    nets.append(ipaddress.ip_network(item.strip(), strict=False))
                except ValueError:
                    continue  # a typo must not open the door: the entry is ignored
        return nets

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
