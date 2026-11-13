"""Small types shared by the answer pipeline and the curated answers (no imports from either)."""

from dataclasses import dataclass


@dataclass
class ChatSource:
    n: int
    chunk_id: int  # 0 when the source is not a chunk (FAQ, pitch, place)
    title: str | None
    url: str
    category: str | None
    program_id: int | None = None
