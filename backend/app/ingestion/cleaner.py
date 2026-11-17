"""HTML -> readable text, using only the standard library parser.

Boilerplate (menus, header, footer, scripts, cookie bars...) is dropped. Headings become
lines starting with "## " so the chunker can keep section context. Links are collected from
the whole page (including menus) because menus are how the crawler discovers pages.
"""

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

SKIP_TAGS = {
    "script", "style", "noscript", "template", "svg", "iframe", "form", "select",
    "nav", "header", "footer", "aside", "button",
}  # fmt: skip
BOILERPLATE_HINTS = (
    "menu", "nav", "header", "footer", "breadcrumb", "sidebar", "cookie",
    "share", "social", "search", "skip-link", "screen-reader", "copyright", "popup", "modal",
)  # fmt: skip
VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
}  # fmt: skip
BLOCK_TAGS = {
    "p", "div", "section", "article", "main", "li", "ul", "ol", "table", "tr", "td", "th",
    "dl", "dt", "dd", "blockquote", "pre", "figcaption", "address", "br", "hr",
}  # fmt: skip
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
CONTENT_ROOTS = {"html", "body", "main", "article"}


@dataclass
class CleanPage:
    title: str | None
    html_lang: str | None
    text: str
    links: list[str] = field(default_factory=list)


def _is_boilerplate(attrs: dict[str, str | None]) -> bool:
    tokens = f"{attrs.get('class') or ''} {attrs.get('id') or ''}".lower().split()
    if attrs.get("role") in ("navigation", "banner", "contentinfo", "search"):
        return True
    if attrs.get("aria-hidden") == "true":
        return True
    return any(hint in tok for tok in tokens for hint in BOILERPLATE_HINTS)


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[str] = []
        self.title_parts: list[str] = []
        self.html_lang: str | None = None
        self._in_title = False
        self._skip_tag: str | None = None  # tag that opened the skipped region
        self._skip_depth = 0
        self._heading_depth = 0

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = dict(attrs_list)
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"] or "")
        if tag == "html" and attrs.get("lang"):
            self.html_lang = (attrs["lang"] or "")[:2].lower()
        if tag == "title":
            self._in_title = True
        if tag in VOID_TAGS:
            if tag in ("br", "hr") and self._skip_tag is None:
                self.parts.append("\n")
            return
        if self._skip_tag is not None:
            if tag == self._skip_tag:
                self._skip_depth += 1
            return
        # Page roots are never skipped by class: WordPress themes put words like
        # "mega-menu-main-navigation" on <body>, which would drop the whole page.
        if tag in SKIP_TAGS or (tag not in CONTENT_ROOTS and _is_boilerplate(attrs)):
            self._skip_tag, self._skip_depth = tag, 1
            return
        if tag in HEADING_TAGS:
            self._heading_depth += 1
            self.parts.append("\n\n## ")
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if self._skip_tag is not None:
            if tag == self._skip_tag:
                self._skip_depth -= 1
                if self._skip_depth == 0:
                    self._skip_tag = None
            return
        if tag in HEADING_TAGS and self._heading_depth:
            self._heading_depth -= 1
            self.parts.append("\n\n")
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
            return
        if self._skip_tag is not None:
            return
        if self._heading_depth:
            data = data.replace("\n", " ")
        self.parts.append(data)


def _tidy(raw: str) -> str:
    lines: list[str] = []
    seen_recent: list[str] = []
    for line in raw.split("\n"):
        line = re.sub(r"[ \t ​]+", " ", line).strip()
        if line in ("##", ""):
            if lines and lines[-1] != "":
                lines.append("")
            continue
        # Drop lines repeated close together (sliders/tabs often duplicate text).
        if line in seen_recent:
            continue
        seen_recent = (seen_recent + [line])[-30:]
        lines.append(line)
    text = "\n".join(lines).strip()
    return re.sub(r"\n{3,}", "\n\n", text)


def clean_html(html: str) -> CleanPage:
    parser = _Extractor()
    parser.feed(html)
    parser.close()
    title = re.sub(r"\s+", " ", "".join(parser.title_parts)).strip() or None
    return CleanPage(
        title=title,
        html_lang=parser.html_lang,
        text=_tidy("".join(parser.parts)),
        links=parser.links,
    )
