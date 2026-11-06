"""Heading-aware chunking.

Text is split into sections at "## " heading lines. Paragraphs of a section are packed into
chunks of at most `max_words`; each chunk starts with its section heading so it still makes
sense on its own when retrieved. Very small leftovers are merged into the previous chunk.
"""

import re


def _split_long(paragraph: str, max_words: int) -> list[str]:
    words = paragraph.split()
    if len(words) <= max_words:
        return [paragraph]
    sentences = re.split(r"(?<=[.!?؟؛])\s+", paragraph)
    out: list[str] = []
    current: list[str] = []
    for sentence in sentences:
        sw = sentence.split()
        if len(sw) > max_words:  # one huge sentence: hard split on words
            if current:
                out.append(" ".join(current))
                current = []
            out.extend(" ".join(sw[i : i + max_words]) for i in range(0, len(sw), max_words))
            continue
        if len(current) + len(sw) > max_words and current:
            out.append(" ".join(current))
            current = []
        current.extend(sw)
    if current:
        out.append(" ".join(current))
    return out


def _sections(text: str) -> list[tuple[str | None, list[str]]]:
    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        if block.startswith("## "):
            first, _, rest = block.partition("\n")
            sections.append((first[3:].strip() or None, []))
            block = rest.strip()
            if not block:
                continue
        sections[-1][1].extend(line.strip() for line in block.split("\n") if line.strip())
    return [s for s in sections if s[1]]


def chunk_text(text: str, max_words: int = 300, min_words: int = 40) -> list[str]:
    chunks: list[str] = []
    for heading, paragraphs in _sections(text):
        prefix = f"{heading}\n" if heading else ""
        budget = max(max_words - len(prefix.split()), 20)
        pieces = [p for para in paragraphs for p in _split_long(para, budget)]
        current: list[str] = []
        count = 0
        section_chunks: list[str] = []
        for piece in pieces:
            n = len(piece.split())
            if current and count + n > budget:
                section_chunks.append(prefix + "\n".join(current))
                current, count = [], 0
            current.append(piece)
            count += n
        if current:
            leftover = "\n".join(current)
            if (
                section_chunks
                and count < min_words
                and len(section_chunks[-1].split()) + count <= int(max_words * 1.2)
            ):
                section_chunks[-1] += "\n" + leftover
            else:
                section_chunks.append(prefix + leftover)
        chunks.extend(section_chunks)
    return chunks
