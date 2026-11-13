"""Numbers in text (digit-normalised), for the "no invented numbers" checks (CLAUDE.md rule 1)."""

import re

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_NUMBER = re.compile(r"\d+(?:[.,/]\d+)*")


def numbers_in(text_: str) -> set[str]:
    return set(_NUMBER.findall(text_.translate(_ARABIC_DIGITS)))


def invented_numbers(answer: str, passages: list[str]) -> list[str]:
    """Numbers said in the answer that appear in none of the passages (digit-normalised)."""
    known: set[str] = set()
    for p in passages:
        known |= numbers_in(p)
    return sorted(n for n in numbers_in(answer) if n not in known)
