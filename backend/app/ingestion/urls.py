from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

from app.ingestion.config import CrawlConfig


def normalize_url(url: str, cfg: CrawlConfig, base: str | None = None) -> str | None:
    """Absolute, canonical-host, fragment-free URL; None for non-http(s) links."""
    if base:
        url = urljoin(base, url)
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower()
    host = cfg.host_aliases.get(host, host)
    path = parts.path or "/"
    return urlunsplit(("https", host, path, parts.query, ""))


def in_scope(url: str, cfg: CrawlConfig) -> bool:
    parts = urlsplit(url)
    if (parts.hostname or "").lower() not in cfg.allowed_hosts:
        return False
    text = unquote(parts.path + "?" + parts.query).lower()
    if any(term in text for term in cfg.exclude_terms):
        return False
    return any(term in text for term in cfg.include_terms)
