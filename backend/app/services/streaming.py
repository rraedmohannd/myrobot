"""Helpers for speaking an LLM answer while it is still being generated (demo sprint M2).

The model keeps its tested output format, a JSON object {"answer": ..., "found": ..., "used": ...}.
`AnswerExtractor` reads the value of "answer" out of the partial JSON text as it arrives, and
`SpeechPieces` cuts that text into pieces for text-to-speech: a short first piece, then whole
sentences. The complete JSON is still parsed at the end, so `found` and `used` are unchanged.
"""

import json
import re

from app.services.speech import FIRST_PIECE_MAX_WORDS, split_for_speech

_ANSWER_KEY = re.compile(r'"answer"\s*:\s*"')
_SENTENCE_END = re.compile(r"(?<=[.!?؟])\s+")
_BACKSLASH = chr(92)


class AnswerExtractor:
    """Incremental reader for the "answer" string of a streamed JSON object."""

    def __init__(self) -> None:
        self.raw = ""
        self.text = ""  # decoded answer so far
        self.done = False  # the closing quote of the answer was seen
        self._start: int | None = None

    def feed(self, delta: str) -> str:
        """Add raw model output; return the new decoded characters of the answer."""
        if self.done or not delta:
            return ""
        self.raw += delta
        if self._start is None:
            match = _ANSWER_KEY.search(self.raw)
            if match is None:
                return ""
            self._start = match.end()
        fragment, self.done = self._complete_part(self.raw[self._start :])
        try:
            decoded = json.loads(f'"{fragment}"', strict=False)
        except json.JSONDecodeError:
            return ""  # wait for more text
        new = decoded[len(self.text) :]
        self.text = decoded
        return new

    @staticmethod
    def _complete_part(fragment: str) -> tuple[str, bool]:
        """The part of the raw JSON string that can be decoded now, and whether the string has
        ended. An escape sequence that is still incomplete at the end waits for the next call."""
        i = safe = 0
        n = len(fragment)
        while i < n:
            if fragment[i] == '"':
                return fragment[:i], True
            if fragment[i] != _BACKSLASH:
                i += 1
            elif i + 1 >= n:
                break  # a backslash alone
            elif fragment[i + 1] != "u":
                i += 2
            elif i + 6 > n:
                break  # partial unicode escape
            elif fragment[i + 2] in "dD" and fragment[i + 3] in "89abAB":
                if i + 12 > n:
                    break  # high surrogate: wait for its low surrogate
                i += 12
            else:
                i += 6
            safe = i
        return fragment[:safe], False


class SpeechPieces:
    """Cuts streamed text into speech pieces, with the same rules as `split_for_speech`:
    the first piece is short (first sentence, cut at a comma or after `first_words` words when long),
    and every later piece is a complete sentence. A piece is only released when the text after
    it has started, so numbers and words are never cut in half."""

    def __init__(self, first_words: int = FIRST_PIECE_MAX_WORDS) -> None:
        self.buffer = ""
        self.emitted = 0
        self.first_words = first_words

    def feed(self, text: str) -> list[str]:
        self.buffer += text
        return self._take(final=False)

    def flush(self) -> list[str]:
        return self._take(final=True)

    def _take(self, final: bool) -> list[str]:
        out: list[str] = []
        while True:
            self.buffer = self.buffer.lstrip()
            match = _SENTENCE_END.search(self.buffer)
            if match is not None:
                sentence, self.buffer = self.buffer[: match.start()], self.buffer[match.end() :]
            elif final and self.buffer.strip():
                sentence, self.buffer = self.buffer.strip(), ""
            else:
                break
            if self.emitted == 0:
                pieces = split_for_speech(sentence, self.first_words)  # may cut a long first sentence in two
            else:
                pieces = [sentence]
            out.extend(pieces)
            self.emitted += len(pieces)
        if not final and self.emitted == 0 and not out:
            early = self._early_first_piece()
            if early:
                out.append(early)
                self.emitted += 1
        return out

    def _early_first_piece(self) -> str | None:
        """A long first sentence: release its first words before the sentence has ended."""
        words = self.buffer.split()
        if self.buffer and not self.buffer[-1].isspace():
            words = words[:-1]  # the last word may still be growing
        if len(words) <= self.first_words + 2:
            return None
        first, rest = split_for_speech(" ".join(words) + " …", self.first_words)[:2]  # same cut as the full rule
        if len(first.split()) >= len(words):
            return None
        remaining = self.buffer.split(None, len(first.split()))
        self.buffer = remaining[-1] if len(remaining) > len(first.split()) else ""
        return first
