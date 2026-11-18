"""Application settings, loaded from environment variables / .env (never hard-coded)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Example values that must never be used as real secrets (.env.example, docs).
EXAMPLE_SECRET_MARKERS = ("change-me", "changeme", "example", "replace-me")


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
    # TTS adapter: "openai" = /v1/audio/speech with TTS_MODEL (the default; gpt-4o-mini-tts is
    # shut down by OpenAI on 2027-01-06), "openai_realtime" = TTS_REALTIME_MODEL through the
    # Realtime API, "elevenlabs" = ElevenLabs (paid credits). See docs/PLAN.md.
    tts_provider: Literal["openai", "openai_realtime", "elevenlabs"] = "openai"
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
    # TTS_PROVIDER=openai_realtime: the model OpenAI names as the replacement of the TTS models.
    # It uses TTS_VOICE (alloy, ash, ballad, coral, echo, sage, shimmer, verse, marin, cedar),
    # TTS_INSTRUCTIONS and TTS_SPEED (at most 1.5).
    tts_realtime_model: str = "gpt-realtime-2.1-mini"
    # TTS_PROVIDER=elevenlabs: key, voice id from the ElevenLabs voice library, model id.
    elevenlabs_api_key: SecretStr | None = None
    elevenlabs_voice_id: str = ""
    elevenlabs_model: str = "eleven_multilingual_v2"
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
    # The bring-up checklist read by the admin "Bring-up" tab (docs/ is mounted at /docs).
    bringup_doc_path: str = "/docs/BRINGUP.md"

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

    # --- Server mode (sprint 6, APP_ENV=production). Everything below changes behaviour only in
    # production unless it is set explicitly; the laptop setup stays as it was. ---
    # In production X-Admin-Token is refused (login only) unless this is true.
    admin_token_scripts: bool = False
    # Signs the visitor cookie (HMAC-SHA256). Required (>= 32 characters) in production.
    secret_key: SecretStr | None = None
    # true = visitors may use /robot and /voice after typing SITE_ACCESS_CODE on /access.
    # false = only the robot token works (the pages answer 404 to everyone else).
    public_pages: bool = True
    site_access_code: SecretStr | None = None
    visitor_session_hours: int = Field(default=12, ge=1)
    # Cost guard. None (unset) = off in development/test; production defaults 500 / 12 / 60.
    daily_paid_call_limit: int | None = Field(default=None, ge=0)  # every real provider call
    rate_limit_paid_per_min: int | None = Field(default=None, ge=0)  # per IP, visitors
    rate_limit_robot_per_min: int | None = Field(default=None, ge=0)  # per IP, robot token
    # Wrong admin passwords before a lockout (per username; in production also per IP).
    login_max_failures: int = Field(default=5, ge=1)
    login_lockout_min: int | None = Field(default=None, ge=1)  # None: 5 (dev) / 15 (production)
    media_max_mb: int | None = Field(default=None, ge=1)  # None = 50
    # Deterministic SIMULATED providers (no OpenAI): rehearsals only. Logged loudly.
    fake_providers: bool = False
    # Read by the startup check in production (from the env; else the DATABASE_URL password).
    postgres_password: SecretStr | None = None
    # Admin "Server" card: last-backup.json of the backup service, Caddy's certificates.
    backups_dir: str = "/backups"
    caddy_data_dir: str = "/caddy-data"

    @model_validator(mode="after")
    def _production_defaults(self) -> "Settings":
        if self.app_env == "production":
            if self.daily_paid_call_limit is None:
                self.daily_paid_call_limit = 500
            if self.rate_limit_paid_per_min is None:
                self.rate_limit_paid_per_min = 12
            if self.rate_limit_robot_per_min is None:
                self.rate_limit_robot_per_min = 60
            if self.login_lockout_min is None:
                self.login_lockout_min = 15
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def login_lockout_s(self) -> int:
        return (self.login_lockout_min or 5) * 60

    @property
    def media_max_bytes(self) -> int | None:
        return self.media_max_mb * 1024 * 1024 if self.media_max_mb else None

    @property
    def public_origin(self) -> str:
        return self.public_base_url.strip().rstrip("/")

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
        if self.is_production:  # the public address only (CORS_ORIGINS is ignored)
            return [self.public_origin] if self.public_origin else []
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


def _secret(value: SecretStr | None) -> str:
    return value.get_secret_value() if value is not None else ""


def _looks_like_example(value: str) -> bool:
    low = value.lower()
    return any(marker in low for marker in EXAMPLE_SECRET_MARKERS)


def production_problems(settings: Settings) -> list[str]:
    """What stops the backend from starting in production. Each line names a setting, never
    its value. Empty outside production."""
    if not settings.is_production:
        return []
    problems: list[str] = []
    admin = _secret(settings.admin_token)
    if len(admin) < 32 or _looks_like_example(admin):
        problems.append(
            "ADMIN_TOKEN must be at least 32 characters and not an example value "
            "(generate one: openssl rand -hex 32)"
        )
    if not _secret(settings.robot_token):
        problems.append("ROBOT_TOKEN is missing")
    elif _looks_like_example(_secret(settings.robot_token)):
        problems.append("ROBOT_TOKEN is an example value")
    password = _secret(settings.postgres_password)
    if not password:
        from sqlalchemy.engine import make_url

        try:
            password = make_url(settings.database_url).password or ""
        except Exception:  # noqa: BLE001 - a broken URL fails later, with its own error
            password = ""
    if len(password) < 16 or _looks_like_example(password):
        problems.append("POSTGRES_PASSWORD must be at least 16 characters and not an example value")
    if not settings.public_origin.startswith("https://") or len(settings.public_origin) <= 8:
        problems.append("PUBLIC_BASE_URL must be set and start with https://")
    secret_key = _secret(settings.secret_key)
    if len(secret_key) < 32 or _looks_like_example(secret_key):
        problems.append("SECRET_KEY must be at least 32 characters and not an example value")
    if settings.public_pages and not _secret(settings.site_access_code):
        problems.append("SITE_ACCESS_CODE is missing while PUBLIC_PAGES=true")
    return problems


class ProductionConfigError(RuntimeError):
    pass


def check_production(settings: Settings) -> None:
    """Refuse to start with weak or missing secrets (APP_ENV=production only)."""
    problems = production_problems(settings)
    if problems:
        raise ProductionConfigError(
            "Refusing to start in production (APP_ENV=production):"
            + "".join(f"\n  - {p}" for p in problems)
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
