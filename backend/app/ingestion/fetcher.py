"""Polite HTTP fetching: robots.txt, rate limit, size limit, HTML only."""

import asyncio
import time
from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.ingestion.config import USER_AGENT, CrawlConfig


@dataclass
class FetchResult:
    url: str
    status: int  # HTTP status; 0 = network error; -1 = blocked by robots.txt
    content_type: str = ""
    html: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.html is not None


class Fetcher:
    def __init__(self, cfg: CrawlConfig, client: httpx.AsyncClient | None = None) -> None:
        self.cfg = cfg
        self._own_client = client is None
        self.client = client or httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT},
            timeout=cfg.timeout_s,
            follow_redirects=True,
        )
        self._robots: dict[str, RobotFileParser] = {}
        self._last_request = 0.0

    async def __aenter__(self) -> "Fetcher":
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._own_client:
            await self.client.aclose()

    async def _wait_turn(self) -> None:
        wait = self.cfg.delay_s - (time.monotonic() - self._last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_request = time.monotonic()

    async def _robots_for(self, url: str) -> RobotFileParser:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            rp = RobotFileParser()
            await self._wait_turn()
            try:
                r = await self.client.get(origin + "/robots.txt")
                if r.status_code == 200:
                    rp.parse(r.text.splitlines())
                elif r.status_code in (401, 403):
                    rp.disallow_all = True
                else:
                    rp.allow_all = True
            except httpx.HTTPError:
                rp.allow_all = True  # robots.txt unreachable: treat as no restrictions
            self._robots[origin] = rp
        return self._robots[origin]

    async def fetch(self, url: str) -> FetchResult:
        robots = await self._robots_for(url)
        if not robots.can_fetch(USER_AGENT, url):
            return FetchResult(url=url, status=-1, error="blocked by robots.txt")
        await self._wait_turn()
        try:
            r = await self.client.get(url)
        except httpx.HTTPError as e:
            return FetchResult(url=url, status=0, error=f"{type(e).__name__}: {e}")
        ctype = r.headers.get("content-type", "")
        if r.status_code != 200:
            return FetchResult(url=url, status=r.status_code, content_type=ctype)
        if "html" not in ctype.lower():
            return FetchResult(
                url=url, status=r.status_code, content_type=ctype, error="not an HTML page"
            )
        if len(r.content) > self.cfg.max_bytes:
            return FetchResult(url=url, status=r.status_code, content_type=ctype, error="too large")
        return FetchResult(url=url, status=200, content_type=ctype, html=r.text)
