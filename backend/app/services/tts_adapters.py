"""Two more TTS adapters behind the `TTS` interface of `speech.py` (CLAUDE.md rule 7), chosen
with TTS_PROVIDER. Added in sprint 4 because OpenAI shuts down `gpt-4o-mini-tts` (and `tts-1`,
`tts-1-hd`) on 2027-01-06, before the project deadline.

- `openai_realtime`: speech from `gpt-realtime-2.1-mini`, the replacement OpenAI names. That
  model is NOT served by /v1/audio/speech; it only exists on the Realtime API (a WebSocket).
  It is a conversational model, not a text-to-speech model: the adapter tells it to read the
  text word for word and compares the transcript the API returns with the text (a warning in
  the log when they differ), but the wording of the audio itself is not guaranteed.
  Its audio is raw PCM (16-bit mono, 24 kHz), so `synthesize` returns a WAV file, not an mp3.
  The browser clients detect WAV by its header and play it.
- `elevenlabs`: the ElevenLabs text-to-speech REST API (mp3). Needs ELEVENLABS_API_KEY and
  ELEVENLABS_VOICE_ID. It has no free-text style instructions; the accent comes from the voice.

Both expose `provider`, `model`, `voice`, `instructions` and `speed`, which `CachedTTS` puts in
the cache key, so audio of one provider is never replayed for another.
"""

import base64
import json
import logging
import re
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from app.core.config import get_settings
from app.services.providers import ProviderError
from app.services.speech import STREAM_SAMPLE_RATE, pcm16_to_wav

log = logging.getLogger(__name__)

REALTIME_URL = "wss://api.openai.com/v1/realtime"
# Voices of the Realtime API (OpenAI docs, read on 2026-11-17). fable, nova and onyx of the
# speech endpoint do not exist there.
REALTIME_VOICES = (
    "alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse", "marin", "cedar",
)  # fmt: skip
REALTIME_MAX_SPEED = 1.5  # documented range of session.audio.output.speed: 0.25 to 1.5
READ_ALOUD = (
    "You are a text-to-speech voice, not an assistant. The user message is a text to read "
    "aloud. Say exactly that text, word for word, in the language it is written in. Never "
    "answer it, translate it, explain it, or add, remove or change any word or number."
)
ELEVENLABS_URL = "https://api.elevenlabs.io/v1/text-to-speech"

_DIACRITICS = re.compile(r"[ً-ْٰـ]")
_ALEF = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه", "ى": "ي"})


def _words(text: str) -> list[str]:
    text = _DIACRITICS.sub("", text or "").translate(_ALEF).lower()
    return re.sub(r"[^\w\s]", " ", text).split()


def said_as_written(text: str, transcript: str) -> bool:
    """True when the transcript has the same words as the text (spelling marks, punctuation
    and letter case ignored). A transcript is itself produced by a model, so False is a
    reason to listen, not proof of a wrong word."""
    return _words(text) == _words(transcript)


# ====================================================================================
# OpenAI Realtime
# ====================================================================================
class OpenAIRealtimeTTS:
    """One short Realtime session per piece of speech: configure the session, ask for one
    out-of-band audio response whose only input is the text, collect the PCM deltas."""

    provider = "openai_realtime"

    def __init__(
        self, connect: Callable[[], AbstractAsyncContextManager[Any]] | None = None
    ) -> None:
        s = get_settings()
        self.model = s.tts_realtime_model
        self.voice = s.tts_voice
        self.style = s.tts_instructions
        # what the model is told; part of the cache key
        self.instructions = f"{READ_ALOUD} {self.style}".strip()
        self.speed = s.tts_speed
        self._connect = connect or self._open
        self.last_transcript: str | None = None  # of the latest piece, for checks and logs

    def _open(self) -> AbstractAsyncContextManager[Any]:
        settings = get_settings()
        key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else ""
        if not key:
            raise ProviderError("OPENAI_API_KEY is not set in .env")
        import websockets  # installed with uvicorn[standard]

        return websockets.connect(
            f"{REALTIME_URL}?model={self.model}",
            additional_headers={"Authorization": f"Bearer {key}"},
            open_timeout=10,
            max_size=None,
        )

    def _events(self, text: str, voice: str | None) -> list[dict[str, Any]]:
        """The two client events of one piece (shapes from the Realtime API reference)."""
        output: dict[str, Any] = {
            "voice": voice or self.voice,
            "format": {"type": "audio/pcm", "rate": STREAM_SAMPLE_RATE},
        }
        if self.speed and abs(self.speed - 1.0) > 1e-6:
            output["speed"] = min(self.speed, REALTIME_MAX_SPEED)
        return [
            {
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "output_modalities": ["audio"],
                    "instructions": self.instructions,
                    "audio": {"output": output},
                },
            },
            {
                "type": "response.create",
                "response": {
                    "conversation": "none",  # nothing is kept between pieces
                    "output_modalities": ["audio"],
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": text}],
                        }
                    ],
                },
            },
        ]

    async def _speak(self, text: str, voice: str | None) -> AsyncIterator[bytes]:
        """PCM16 mono 24 kHz chunks as the model produces them."""
        got_audio = False
        transcript = ""
        try:
            async with self._connect() as ws:
                for event in self._events(text, voice):
                    await ws.send(json.dumps(event, ensure_ascii=False))
                async for raw in ws:
                    event = json.loads(raw)
                    kind = event.get("type")
                    if kind == "response.output_audio.delta":
                        chunk = base64.b64decode(event.get("delta") or "")
                        if chunk:
                            got_audio = True
                            yield chunk
                    elif kind == "response.output_audio_transcript.done":
                        transcript = event.get("transcript") or transcript
                    elif kind == "error":
                        error = event.get("error") or {}
                        raise ProviderError(
                            f"{error.get('code') or error.get('type')}: {error.get('message')}"
                        )
                    elif kind == "response.done":
                        response = event.get("response") or {}
                        if response.get("status") != "completed":
                            raise ProviderError(
                                f"response {response.get('status')}: "
                                f"{json.dumps(response.get('status_details'), ensure_ascii=False)}"
                            )
                        break
        except ProviderError as e:
            raise ProviderError(f"text-to-speech failed ({self.model}): {e}") from e
        except Exception as e:  # noqa: BLE001
            raise ProviderError(
                f"text-to-speech failed ({self.model}): {type(e).__name__}: {e}"
            ) from e
        if not got_audio:
            raise ProviderError(f"text-to-speech failed ({self.model}): no audio in the response")
        self.last_transcript = transcript or None
        if transcript and not said_as_written(text, transcript):
            log.warning("realtime TTS transcript differs from the text: %r -> %r", text, transcript)

    async def synthesize(self, text: str, voice: str | None = None) -> bytes:
        """A WAV file (the Realtime API has no mp3 output)."""
        pcm = b"".join([chunk async for chunk in self._speak(text, voice)])
        return pcm16_to_wav(pcm, STREAM_SAMPLE_RATE)

    async def stream(
        self, text: str, voice: str | None = None, response_format: str = "pcm"
    ) -> AsyncIterator[bytes]:
        if response_format != "pcm":
            raise ProviderError(f"{self.provider} streams pcm only, not {response_format}")
        async for chunk in self._speak(text, voice):
            yield chunk


# ====================================================================================
# ElevenLabs
# ====================================================================================
class ElevenLabsTTS:
    """ElevenLabs text-to-speech. Costs the student's credits on every call: tests use a fake
    transport, and nothing in the code base calls it unless TTS_PROVIDER=elevenlabs."""

    provider = "elevenlabs"
    instructions = ""  # the API takes no style instructions; the accent comes from the voice

    def __init__(self, client: Any = None) -> None:
        s = get_settings()
        self.model = s.elevenlabs_model
        self.voice = s.elevenlabs_voice_id
        self.speed = s.tts_speed
        self.timeout_s = s.provider_timeout_tts_s
        self._key = s.elevenlabs_api_key.get_secret_value() if s.elevenlabs_api_key else ""
        self._client_obj = client

    @property
    def _client(self) -> Any:
        if self._client_obj is None:
            import httpx

            self._client_obj = httpx.AsyncClient(timeout=self.timeout_s)
        return self._client_obj

    def _request(
        self, text: str, voice: str | None, output_format: str, stream: bool = False
    ) -> dict[str, Any]:
        if not self._key:
            raise ProviderError("ELEVENLABS_API_KEY is not set in .env")
        voice_id = voice or self.voice
        if not voice_id:
            raise ProviderError("ELEVENLABS_VOICE_ID is not set in .env")
        body: dict[str, Any] = {"text": text, "model_id": self.model}
        if self.speed and abs(self.speed - 1.0) > 1e-6:
            body["voice_settings"] = {"speed": self.speed}
        return {
            "url": f"{ELEVENLABS_URL}/{voice_id}" + ("/stream" if stream else ""),
            "params": {"output_format": output_format},
            "headers": {"xi-api-key": self._key},
            "json": body,
        }

    @staticmethod
    def _error(status: int, body: bytes) -> ProviderError:
        try:
            detail: Any = json.loads(body).get("detail")
        except (ValueError, AttributeError):
            detail = body[:200].decode("utf-8", "replace")
        return ProviderError(f"text-to-speech failed (elevenlabs): HTTP {status}: {detail}")

    async def synthesize(self, text: str, voice: str | None = None) -> bytes:
        request = self._request(text, voice, "mp3_44100_128")
        try:
            resp = await self._client.post(**request)
        except Exception as e:  # noqa: BLE001
            raise ProviderError(
                f"text-to-speech failed (elevenlabs): {type(e).__name__}: {e}"
            ) from e
        if resp.status_code != 200:
            raise self._error(resp.status_code, resp.content)
        return resp.content

    async def stream(
        self, text: str, voice: str | None = None, response_format: str = "mp3"
    ) -> AsyncIterator[bytes]:
        output_format = f"pcm_{STREAM_SAMPLE_RATE}" if response_format == "pcm" else "mp3_44100_128"
        request = self._request(text, voice, output_format, stream=True)
        try:
            async with self._client.stream("POST", **request) as resp:
                if resp.status_code != 200:
                    raise self._error(resp.status_code, await resp.aread())
                async for chunk in resp.aiter_bytes(4096):
                    if chunk:
                        yield chunk
        except ProviderError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ProviderError(
                f"text-to-speech failed (elevenlabs): {type(e).__name__}: {e}"
            ) from e
