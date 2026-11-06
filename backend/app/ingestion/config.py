"""Crawl configuration: where to start, what is in scope (MEU Faculty of Engineering only)."""

from dataclasses import dataclass, field

USER_AGENT = "MEU-ReceptionRobot-GP2/0.1 (student graduation project; polite crawler)"

# Starting pages (English + Arabic). Edit this list to add more official engineering pages.
DEFAULT_SEEDS: tuple[str, ...] = (
    "https://www.meu.edu.jo/faculties/faculty-of-engineering/",
    "https://www.meu.edu.jo/faculties/faculty-of-engineering/?lang=ar",
    "https://www.meu.edu.jo/faculty-of-engineering-departments/",
    "https://www.meu.edu.jo/faculty-of-engineering-programs/",
    "https://www.meu.edu.jo/faculty-of-engineering-staff/",
    "https://www.meu.edu.jo/engineering-dean-message/",
    "https://www.meu.edu.jo/%D8%A7%D9%84%D9%83%D9%84%D9%8A%D8%A7%D8%AA/"
    "%D9%83%D9%84%D9%8A%D8%A9-%D8%A7%D9%84%D9%87%D9%86%D8%AF%D8%B3%D8%A9/?lang=ar",
)


@dataclass(frozen=True)
class CrawlConfig:
    seeds: tuple[str, ...] = DEFAULT_SEEDS
    allowed_hosts: frozenset[str] = frozenset({"www.meu.edu.jo"})
    # Hosts rewritten to the canonical host before fetching (site links point to dev.).
    host_aliases: dict[str, str] = field(
        default_factory=lambda: {"meu.edu.jo": "www.meu.edu.jo", "dev.meu.edu.jo": "www.meu.edu.jo"}
    )
    # A followed link must contain one of these (checked on the decoded, lower-case path).
    # Scope = Faculty of Engineering & Design (engineering programs, architecture, design).
    include_terms: tuple[str, ...] = (
        "engineering", "هندس", "architecture", "design", "عمارة", "العمارة", "تصميم",
    )  # fmt: skip
    # ...and none of these: other faculties/units (CLAUDE.md rule 3) and WordPress noise.
    exclude_terms: tuple[str, ...] = (
        "engineering-services", "الخدمات",
        "software-engineering", "البرمجيات",
        "wp-", "/feed", "/tag/", "/author/", "/category/", "replytocom",
    )  # fmt: skip
    max_pages: int = 60
    max_depth: int = 2
    delay_s: float = 1.0  # pause between requests (politeness)
    timeout_s: float = 20.0
    max_bytes: int = 3_000_000
    # Chunk size in words (~300-500 tokens).
    chunk_max_words: int = 300
    chunk_min_words: int = 40
