"""Screen payloads for `display.update` (docs/PROTOCOL.md): the answer card.

The card only re-uses what is already in the grounded answer and its sources: the lines are
sentences of the answer, the title is the name of the program the sources belong to (or the
faculty name), and the source line lists the source titles. Nothing here creates new facts
(CLAUDE.md rule 1).
"""

import re
from collections.abc import Sequence
from typing import Any, Protocol

CARD_MAX_LINES = 3
CARD_LINE_MAX_CHARS = 90
FACULTY_NAME = {"ar": "كلية الهندسة والتصميم", "en": "Faculty of Engineering & Design"}


class HasProgram(Protocol):
    program_id: int | None


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?؟])\s+", text.strip()) if s.strip()]


def _shorten(line: str, limit: int = CARD_LINE_MAX_CHARS) -> str:
    if len(line) <= limit:
        return line
    cut = line[:limit].rsplit(" ", 1)[0].rstrip("،,;؛:")
    return cut + "…"


def dominant_program(items: Sequence[HasProgram]) -> int | None:
    """The single program these passages belong to, or None when they are mixed or general.

    "Clearly one program" = exactly one program id appears, on at least half of the passages.
    """
    ids = [i.program_id for i in items if i.program_id is not None]
    if not ids or len(set(ids)) != 1 or len(ids) * 2 < len(items):
        return None
    return ids[0]


def build_card(
    answer: str,
    source_titles: Sequence[str | None],
    lang: str,
    program_name: str | None = None,
    media: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Card for a found answer: title, at most 3 short lines, source titles, optional media."""
    titles = [t for t in dict.fromkeys(source_titles) if t]
    if not answer.strip() or not titles:
        return None
    card: dict[str, Any] = {
        "title": program_name or FACULTY_NAME.get(lang, FACULTY_NAME["ar"]),
        "lines": [_shorten(s) for s in split_sentences(answer)[:CARD_MAX_LINES]],
        "sources": titles,
    }
    if media:
        card["media"] = media
    return card
