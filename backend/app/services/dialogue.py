"""Conversation manager for one robot connection (B4).

States: IDLE -> GREETING -> LISTENING -> ANSWERING -> FOLLOW_UP ("عندك سؤال ثاني؟") -> CLOSING -> IDLE
Intents (rule-based, instant): question, done/thanks, greeting, repeat, unclear.

Turn-taking: audio is only accepted in LISTENING / FOLLOW_UP; the robot mutes its microphone
while it plays our audio and sends `listen.timeout` if nobody speaks after the follow-up.
Safety never depends on this module (CLAUDE.md rule 4).
"""

import asyncio
import base64
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.db.models import Program, Session, Turn
from app.ingestion.lang import detect_lang
from app.services.chat import UNAVAILABLE, ChatResult, answer_events
from app.services.display import build_card, dominant_program
from app.services.media import approved_media_for_program
from app.services.providers import LLM, Embedder, ProviderError
from app.services.speakers import (
    Assignment,
    NullSpeakerEmbedder,
    SpeakerEmbedder,
    SpeakerTracker,
)
from app.services.speech import STT, CachedTTS, pcm16_to_wav, split_for_speech

PROTOCOL_VERSION = 1

PHRASES: dict[str, dict[str, str]] = {
    "greeting": {
        "ar": "أهلاً وسهلاً فيك! أنا روبوت كلية الهندسة والتصميم في جامعة الشرق الأوسط. "
        "شو حابب تعرف عن تخصصاتنا؟",
        "en": "Welcome! I'm the reception robot of the Faculty of Engineering and Design at "
        "Middle East University. What would you like to know?",
    },
    "greeting_reply": {
        "ar": "أهلين فيك! شو حابب تعرف عن الكلية؟",
        "en": "Hi there! What would you like to know about the faculty?",
    },
    "follow_up": {"ar": "عندك سؤال ثاني؟", "en": "Do you have another question?"},
    "goodbye": {
        "ar": "تشرفنا فيك! بالتوفيق إن شاء الله، ومع السلامة.",
        "en": "It was a pleasure. Good luck, and goodbye!",
    },
    "timeout_goodbye": {
        "ar": "شكلك خلصت أسئلتك. أهلاً وسهلاً فيك بأي وقت، مع السلامة!",
        "en": "Looks like you're all set. You're welcome anytime, goodbye!",
    },
    "unclear": {
        "ar": "عذراً، ما سمعتك منيح. ممكن تعيد؟",
        "en": "Sorry, I didn't catch that. Could you say it again?",
    },
    "new_speaker": {"ar": "أهلين فيك كمان!", "en": "Hi, welcome to you too!"},
    "filler_0": {"ar": "سؤال حلو، لحظة بس.", "en": "Good question, one moment."},
    "filler_1": {"ar": "خليني أشوفلك.", "en": "Let me check that for you."},
    "filler_2": {"ar": "ثانية بس.", "en": "Just a second."},
    "one_at_a_time": {
        "ar": "لو سمحتوا واحد واحد، عشان أقدر أسمعكم منيح. تفضّلوا.",
        "en": "One at a time please, so I can hear you well. Go ahead.",
    },
}

# ---------------- intents ----------------
_ALEF = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه", "ى": "ي", "ـ": None})
DONE = {
    "شكرا", "شكرا جزيلا", "يسلمو", "يسلموا", "يسلمك", "مشكور", "مشكوره", "تسلم", "تسلمي",
    "خلص", "بس هيك", "لا شكرا", "لا خلص", "لا", "لأ", "لا ما في", "ما في", "ما عندي",
    "مع السلامه", "باي", "يعطيك العافيه", "الله يعطيك العافيه", "خلص شكرا",
    "no", "no thanks", "no thank you", "thats all", "that's all", "bye", "goodbye",
    "thank you", "thanks", "nothing", "im done", "i'm done", "nope",
}  # fmt: skip
GREETINGS = {
    "مرحبا", "مرحبتين", "هاي", "هلا", "اهلين", "اهلا", "السلام عليكم", "صباح الخير",
    "مسا الخير", "مساء الخير", "hi", "hello", "hey", "good morning", "good evening",
}  # fmt: skip
REPEAT = {
    "عيد", "عيدها", "اعيد", "عيد كمان مره", "ما سمعت", "ما سمعتك", "ما فهمت", "شو حكيت",
    "مره ثانيه", "repeat", "again", "say that again", "pardon", "what did you say",
}  # fmt: skip
QUESTION_WORDS = {
    "كم", "شو", "وين", "مين", "ايش", "متى", "امتى", "ليش", "كيف", "هل", "قديش", "اي", "شلون",
    "what", "how", "where", "when", "who", "which", "why", "is", "are", "do", "does", "can",
    "tell",
}  # fmt: skip


DONE_PREFIXES = (
    "شكرا", "يسلمو", "مشكور", "تسلم", "مع السلامه", "باي", "يعطيك العافيه", "الله يعطيك",
    "لا شكرا", "خلص شكرا", "thank", "thanks", "bye", "goodbye", "no thanks",
)  # fmt: skip


def _norm(text: str) -> str:
    text = re.sub(r"[\u064B-\u0652\u0670]", "", text)  # Arabic diacritics
    text = text.translate(_ALEF).lower()
    text = re.sub(r"[^\w\s']", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def classify(text: str, state: str) -> str:
    """Cheap, predictable intent rules; anything that looks like a question is a question."""
    raw = text.strip()
    norm = _norm(raw)
    if len(re.sub(r"\W", "", norm)) < 2:
        return "unclear"
    words = norm.split()
    asks = "?" in raw or "؟" in raw or any(w.lstrip("و") in QUESTION_WORDS for w in words)
    if asks or len(words) > 5:
        return "question"
    if set(words) & REPEAT or any(p in norm for p in REPEAT if " " in p):
        return "repeat"
    if norm in DONE or (state == "FOLLOW_UP" and words[0] in {"لا", "no", "nope"} and len(words) <= 3):
        return "done"
    if len(words) <= 4 and norm.startswith(DONE_PREFIXES):
        return "done"
    if len(words) <= 3 and (norm in GREETINGS or norm.startswith(tuple(GREETINGS))):
        return "greeting"
    return "question"


# ---------------- manager ----------------
Send = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class TurnTimings:
    stt_ms: int | None = None
    retrieval_ms: int | None = None
    llm_ms: int | None = None  # whole LLM call
    llm_first_ms: int | None = None  # LLM start -> first speakable piece (streaming)
    ack_ms: int | None = None  # end of speech -> first audio of any kind (filler / greeting)
    tts_ms: int | None = None  # end of speech -> first audio chunk of the ANSWER
    total_ms: int | None = None  # = tts_ms (answer latency, reported honestly apart from fillers)


@dataclass
class DialogueManager:
    send: Send
    sessionmaker: async_sessionmaker[AsyncSession]
    embedder: Embedder
    llm: LLM
    stt: STT
    tts: CachedTTS
    state: str = "IDLE"
    session_id: int | None = None
    lang: str = "ar"
    last_answer: str | None = None
    audio: bytearray = field(default_factory=bytearray)
    sample_rate: int = 16000
    speaker_embedder: SpeakerEmbedder = field(default_factory=NullSpeakerEmbedder)
    tracker: SpeakerTracker | None = None
    speaker: Assignment | None = None  # who spoke the utterance being handled
    filler_turn: int = 0
    media_turn: int = 0
    robot_run_id: int | None = None  # set by /ws/robot while a robot run is being recorded
    _warm_up: asyncio.Task[None] | None = None

    # ---------- plumbing ----------
    async def _set_state(self, state: str) -> None:
        self.state = state
        await self.send({"type": "dialogue.state", "state": state})

    async def _display(self, mode: str, subtitle: str | None = None, **extra: Any) -> None:
        await self.send({"type": "display.update", "mode": mode, "subtitle": subtitle, **extra})

    async def _speak(self, text: str, cache: bool = False, started: float | None = None) -> int | None:
        """Send `text` as mp3 chunks (first sentence first). Returns ms until first chunk."""
        pieces = split_for_speech(text) or [text]
        tasks = [asyncio.create_task(self.tts.synthesize(p, cache=cache)) for p in pieces]
        first_ms = None
        try:
            for i, (piece, task) in enumerate(zip(pieces, tasks, strict=True)):
                mp3 = await task
                await self.send({
                    "type": "answer.audio", "seq": i, "last": i == len(pieces) - 1,
                    "text": piece, "mp3_b64": base64.b64encode(mp3).decode(),
                })  # fmt: skip
                if first_ms is None and started is not None:
                    first_ms = int((time.perf_counter() - started) * 1000)
        except ProviderError as e:
            for t in tasks:
                t.cancel()
            await self.send({"type": "error", "code": "tts_failed", "message": str(e)})
        return first_ms

    async def _say(self, key: str, display_mode: str = "answering") -> None:
        text = PHRASES[key][self.lang]
        await self._display(display_mode, text)
        await self._speak(text, cache=True)

    # ---------- message entry point ----------
    async def handle(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind == "hello":
            await self.send({
                "type": "hello.ack", "protocol_version": PROTOCOL_VERSION,
                "follow_up_timeout_s": get_settings().follow_up_timeout_s,
                "max_utterance_s": get_settings().max_utterance_s,
            })  # fmt: skip
        elif kind == "heartbeat":
            await self.send({"type": "heartbeat"})
        elif kind == "session.start":
            await self.start_session(msg.get("lang") or "ar")
        elif kind == "audio.chunk":
            if self.state in ("LISTENING", "FOLLOW_UP"):
                self.sample_rate = int(msg.get("sample_rate") or 16000)
                self.audio.extend(base64.b64decode(msg.get("pcm16_b64") or ""))
                if len(self.audio) > get_settings().max_utterance_s * self.sample_rate * 2:
                    await self.end_of_speech()
        elif kind == "audio.end":
            if self.state in ("LISTENING", "FOLLOW_UP"):
                track = msg.get("face_track_id")
                await self.end_of_speech(
                    face_track=int(track) if isinstance(track, int | float) else None,
                    overlap=bool(msg.get("overlap")),
                )
        elif kind == "text.input":
            if self.session_id is None:
                await self.start_session(detect_lang(msg.get("text") or ""), greet=False)
            await self.handle_text(str(msg.get("text") or ""), time.perf_counter(), None)
        elif kind == "listen.timeout":
            if self.state in ("LISTENING", "FOLLOW_UP"):
                await self.close_session("timeout", "timeout_goodbye")
        elif kind == "session.end":
            reason = msg.get("reason") if msg.get("reason") in ("user_done", "lost_face", "timeout") else "user_done"
            await self.close_session(reason, None)
        else:
            await self.send({"type": "error", "code": "unknown_type", "message": f"unknown type {kind!r}"})

    # ---------- session ----------
    async def start_session(self, lang: str, greet: bool = True) -> None:
        if self.session_id is not None:
            await self.close_session("lost_face", None)
        self.lang = lang if lang in ("ar", "en") else "ar"
        async with self.sessionmaker() as db:
            s = Session(lang_detected=self.lang, robot_run_id=self.robot_run_id)
            db.add(s)
            await db.commit()
            self.session_id = s.id
        await self.send({"type": "session.started", "session_id": self.session_id})
        self.audio.clear()
        self.last_answer = None
        settings = get_settings()
        self.tracker = SpeakerTracker(
            settings.speaker_threshold, settings.max_speakers,
            new_threshold=settings.speaker_new_threshold,
        )  # fmt: skip
        # Open the provider connection while the greeting plays, so the first question is not
        # slowed down by the TLS handshake (one tiny embedding request; failures do not matter).
        self._warm_up = asyncio.create_task(self._warm())
        if greet:
            await self._set_state("GREETING")
            await self._say("greeting", "attentive")
        await self._set_state("LISTENING")
        await self._display("listening")

    async def _warm(self) -> None:
        try:
            await self.embedder.embed(["مرحبا"])
        except Exception:  # noqa: BLE001, S110 - only a warm-up
            pass

    async def close_session(self, reason: str, phrase_key: str | None) -> None:
        if self.session_id is None:
            return
        await self._set_state("CLOSING")
        if phrase_key:
            await self._say(phrase_key, "finished")
        async with self.sessionmaker() as db:
            s = await db.get(Session, self.session_id)
            if s is not None:
                s.ended_at = datetime.now(UTC)
                s.end_reason = reason
                s.lang_detected = self.lang
                await db.commit()
        await self.send({"type": "session.ended", "session_id": self.session_id, "reason": reason})
        self.session_id = None
        self.tracker = None  # voiceprints are forgotten with the conversation
        self.audio.clear()
        await self._set_state("IDLE")
        await self._display("idle")

    async def disconnected(self) -> None:
        if self.session_id is not None:
            async with self.sessionmaker() as db:
                s = await db.get(Session, self.session_id)
                if s is not None and s.ended_at is None:
                    s.ended_at, s.end_reason = datetime.now(UTC), "error"
                    await db.commit()
            self.session_id = None

    # ---------- turns ----------
    async def end_of_speech(self, face_track: int | None = None, overlap: bool = False) -> None:
        started = time.perf_counter()
        pcm, self.audio = bytes(self.audio), bytearray()
        state_before = self.state
        if overlap:  # the camera saw two or more people talking at once
            await self._say("one_at_a_time", "listening")
            await self._log_turn("", self.lang, "overlap", PHRASES["one_at_a_time"][self.lang],
                                 TurnTimings(), None, None, None)  # fmt: skip
            await self._set_state(state_before)
            return
        if len(pcm) < self.sample_rate * 2 * 0.3:  # under 0.3 s: nothing to transcribe
            await self.handle_text("", started, None, state_before)
            return
        await self._set_state("ANSWERING")
        await self._display("processing")
        # voiceprint in parallel with speech-to-text, so it adds no latency
        voice_task = asyncio.create_task(
            asyncio.to_thread(self.speaker_embedder.embed, pcm, self.sample_rate)
        )
        try:
            stt = await self.stt.transcribe(pcm16_to_wav(pcm, self.sample_rate))
        except ProviderError as e:
            voice_task.cancel()
            await self.send({"type": "error", "code": "stt_failed", "message": str(e)})
            await self._display("unavailable", UNAVAILABLE[self.lang])
            await self._speak(UNAVAILABLE[self.lang], cache=True)
            await self._set_state(state_before)
            return
        try:
            emb = await voice_task
        except Exception:  # noqa: BLE001 - speaker tracking must never break the conversation
            emb = None
        speaker = self.tracker.assign(emb, face_track) if self.tracker else None
        await self.handle_text(stt.text, started, stt.ms, state_before, speaker)

    async def handle_text(self, text: str, started: float, stt_ms: int | None,
                          state_before: str | None = None,
                          speaker: Assignment | None = None) -> None:  # fmt: skip
        state_before = state_before or self.state
        lang = detect_lang(text) if text.strip() else self.lang
        intent = classify(text, state_before)
        if intent != "unclear":
            self.lang = lang
        self.speaker = speaker
        await self.send({
            "type": "transcript", "text": text, "lang": lang, "intent": intent,
            "speaker": vars(speaker) if speaker else None,
        })  # fmt: skip
        timings = TurnTimings(stt_ms=stt_ms)
        answer_text, used_ids, error = None, None, None
        found: bool | None = None  # only question turns have it

        if intent == "done":
            await self._log_turn(text, lang, intent, PHRASES["goodbye"][self.lang], timings, None, None)
            await self.close_session("user_done", "goodbye")
            return
        if intent == "unclear":
            await self._say("unclear", "listening")
            await self._log_turn(text, lang, intent, PHRASES["unclear"][self.lang], timings, None, None)
            await self._set_state(state_before if state_before in ("LISTENING", "FOLLOW_UP") else "LISTENING")
            return
        if intent == "greeting":
            await self._say("greeting_reply")
            answer_text = PHRASES["greeting_reply"][self.lang]
            await self._log_turn(text, lang, intent, answer_text, timings, None, None)
            await self._set_state("LISTENING")
            await self._display("listening")
            return

        await self._set_state("ANSWERING")
        if intent == "repeat" and self.last_answer:
            answer_text = self.last_answer
            await self._display("answering", answer_text)
            timings.tts_ms = await self._speak(answer_text, started=started)
            timings.ack_ms = timings.tts_ms
        else:
            label = speaker.label if speaker else None
            if speaker and speaker.is_new and speaker.label > 1:
                await self._say("new_speaker", "attentive")  # cached: instant, and covers latency
                timings.ack_ms = int((time.perf_counter() - started) * 1000)
            elif get_settings().filler_enabled:
                key = f"filler_{self.filler_turn % 3}"  # rotate so it does not sound repetitive
                self.filler_turn += 1
                await self._speak(PHRASES[key][self.lang], cache=True)
                timings.ack_ms = int((time.perf_counter() - started) * 1000)
            await self._display("processing", speaker_label=label)
            result, timings.tts_ms = await self._answer(text, label, started)
            answer_text, error = result.spoken or result.answer, result.error
            used_ids = [s.chunk_id for s in result.sources] or None
            timings.retrieval_ms = result.timings.get("retrieval_ms")
            timings.llm_ms = result.timings.get("llm_ms")
            timings.llm_first_ms = result.timings.get("llm_first_ms")
            found = result.found if not error else None
            self.last_answer = answer_text
        timings.total_ms = timings.tts_ms
        await self.send({"type": "turn.timings", **vars(timings)})
        await self._log_turn(text, lang, intent, answer_text, timings, used_ids, error, found=found)
        await self._say("follow_up", "listening")
        await self._set_state("FOLLOW_UP")

    async def _answer(self, text: str, label: int | None, started: float) -> tuple[ChatResult, int | None]:
        """Answer a question and speak it piece by piece while the LLM is still writing.

        Each piece was already checked for invented numbers (services/chat.py) and goes to
        text-to-speech at once; the audio is sent in order. Returns the result and the ms from
        the end of speech to the first audio of the answer.
        """
        queue: asyncio.Queue[tuple[str, asyncio.Task[bytes]] | None] = asyncio.Queue()
        tasks: list[asyncio.Task[bytes]] = []
        first_ms: list[int] = []

        async def sender() -> None:
            seq, item = 0, await queue.get()
            while item is not None:
                piece, task = item
                try:
                    mp3 = await task
                except ProviderError as e:
                    await self.send({"type": "error", "code": "tts_failed", "message": str(e)})
                    return
                following = await queue.get()  # the next piece, or None: this one is the last
                await self.send({
                    "type": "answer.audio", "seq": seq, "last": following is None,
                    "text": piece, "mp3_b64": base64.b64encode(mp3).decode(),
                })  # fmt: skip
                if not first_ms:
                    first_ms.append(int((time.perf_counter() - started) * 1000))
                seq, item = seq + 1, following

        sending = asyncio.create_task(sender())
        result: ChatResult | None = None
        try:
            async with self.sessionmaker() as db:
                async for event in answer_events(
                    db, self.embedder, self.llm, text, session_id=self.session_id, log=False,
                    speaker_label=label,
                ):  # fmt: skip
                    if event.kind == "piece":
                        if not tasks:
                            await self._display("answering", speaker_label=label)
                        tasks.append(asyncio.create_task(
                            self.tts.synthesize(event.text, cache=event.replacement)
                        ))  # fmt: skip
                        queue.put_nowait((event.text, tasks[-1]))
                    elif event.kind == "result":
                        result = event.result
            assert result is not None  # answer_events always ends with the result
            answer_text = result.spoken or result.answer
            await self.send({
                "type": "answer.text", "text": answer_text, "lang": result.lang, "found": result.found,
                "sources": [{"n": s.n, "title": s.title, "url": s.url} for s in result.sources],
                "timings": result.timings, "invented_numbers": result.invented_numbers,
            })  # fmt: skip
            card = await self._card(result) if result.found and not result.error else None
            await self._display("unavailable" if result.error else "answering", answer_text,
                                card=card, speaker_label=label)  # fmt: skip
        finally:
            queue.put_nowait(None)
            await sending
            for task in tasks:
                if not task.done():
                    task.cancel()
                elif not task.cancelled():
                    task.exception()  # a failed piece after the first failure: already reported
        first = first_ms[0] if first_ms else None
        if result.error:  # the service-unavailable line (cached)
            first = await self._speak(result.answer, cache=True, started=started) or first
        return result, first

    async def _card(self, result: ChatResult) -> dict[str, Any] | None:
        """Screen card for a found answer: built only from the answer text and its sources."""
        program_name, media = None, None
        program_id = dominant_program(result.sources)
        if program_id is not None:
            async with self.sessionmaker() as db:
                program = await db.get(Program, program_id)
                # a picture or video only when the answer is clearly about ONE program that has
                # APPROVED media; several files of a program take turns
                media = await approved_media_for_program(db, program_id, result.lang, self.media_turn)
            if program is not None:
                program_name = program.name_ar if result.lang == "ar" else program.name_en
            if media is not None:
                self.media_turn += 1
        return build_card(
            result.spoken or result.answer, [s.title for s in result.sources], result.lang,
            program_name, media,
        )

    async def _log_turn(self, text: str, lang: str, intent: str, answer: str | None,
                        t: TurnTimings, used_ids: list[int] | None, error: str | None,
                        speaker: Assignment | None | str = "current",
                        found: bool | None = None) -> None:  # fmt: skip
        sp = self.speaker if speaker == "current" else speaker
        if self.session_id is None:
            return
        async with self.sessionmaker() as db:
            idx = await db.scalar(
                select(func.count()).select_from(Turn).where(Turn.session_id == self.session_id)
            )
            db.add(Turn(
                session_id=self.session_id, idx=idx or 0, user_text=text, user_lang=lang,
                intent=intent, answer_text=answer, used_chunk_ids=used_ids, stt_ms=t.stt_ms,
                retrieval_ms=t.retrieval_ms, llm_ms=t.llm_ms, tts_ms=t.tts_ms,
                total_ms=t.total_ms, error=error,
                found=found, ack_ms=t.ack_ms, llm_first_ms=t.llm_first_ms,
                speaker_label=sp.label if sp else None,
                speaker_confidence=sp.confidence if sp else None,
                speaker_source=sp.source if sp else None,
            ))  # fmt: skip
            await db.commit()
