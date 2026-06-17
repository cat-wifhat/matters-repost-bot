"""Base abstractions for repost sources.

Each source site (p-articles, newsmarket, foodthink, ...) implements a Source
subclass: how to list its recent articles, how to parse one, how to track
"already seen", and how to wrap the article with site-specific header/credit
HTML on Matters.

The orchestrator (bot/main.py) is source-agnostic and just calls these methods.
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

# Sentinel returned by publish_schedule() meaning "publish immediately".
PUBLISH_NOW = "NOW"


def iso_utc(dt: datetime) -> str:
    """Format a UTC datetime as the ISO-8601 string Matters' publishAt accepts."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

import cloudscraper
import requests

from ..config import USER_AGENT

log = logging.getLogger(__name__)


@dataclass
class ArticleRef:
    """Lightweight pointer to an article — what listings produce.

    `article_id` is opaque to the orchestrator; sources define their own format
    (e.g. "critics/5993" for p-articles, a URL slug for WordPress sites).
    `extra` carries source-specific metadata picked up during listing so the
    source can decide is_new() / advance_state() without re-fetching.
    """
    source: str
    article_id: str
    url: str
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Article:
    source: str
    article_id: str
    url: str
    title: str
    author: str = ""
    date: str = ""              # ISO YYYY-MM-DD if known
    tags: list[str] = field(default_factory=list)
    featured_images: list[str] = field(default_factory=list)
    body_html: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def make_scraper_session(use_cloudscraper: bool = True) -> requests.Session:
    """Build an HTTP session. Use cloudscraper for sites behind Cloudflare,
    plain requests otherwise (slight perf saving)."""
    if use_cloudscraper:
        s = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "darwin", "mobile": False},
        )
    else:
        s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    s.headers["Accept-Language"] = "zh-TW,zh;q=0.9,en;q=0.8"
    return s


def make_curl_cffi_session(impersonate: str = "safari17_0"):
    """Build a curl_cffi session that mimics a real browser's TLS fingerprint.

    Used by sources whose firewall blocks plain requests / cloudscraper /
    chrome-fingerprint sessions but lets safari/firefox through. curl_cffi
    sets its own UA/headers via the impersonation; we don't override them.

    Returns a curl_cffi.requests.Session — quacks like requests.Session
    (supports .get/.post/.headers/.content/.raise_for_status()).
    """
    from curl_cffi import requests as cffi  # local import: optional dep
    return cffi.Session(impersonate=impersonate)


def fetch_image_bytes(
    url: str,
    session: Optional[requests.Session] = None,
) -> tuple[bytes, str]:
    """Download an image and return (bytes, content_type).

    Pass the source's session so we reuse its Cloudflare-bypass token — fetching
    images with a fresh session can re-trigger the JS challenge on every call.
    """
    s = session or make_scraper_session()
    resp = s.get(url, timeout=60)
    resp.raise_for_status()
    content_type = (resp.headers.get("Content-Type") or "image/png").split(";")[0].strip()
    return resp.content, content_type


def fetch_json(
    session,
    url: str,
    *,
    params: Optional[dict] = None,
    timeout: int = 30,
    attempts: int = 3,
    backoff: tuple[int, ...] = (0, 3, 8),
):
    """GET `url` and parse JSON, retrying transient failures.

    WordPress/WAF endpoints behind Cloudflare occasionally return an empty or
    non-JSON 200 body to datacenter IPs (e.g. GitHub Actions runners). A single
    such blip used to crash the whole run at `resp.json()`. We retry on:
    network errors, 5xx, and empty/non-JSON bodies. 4xx is surfaced immediately
    (not transient). Backoff between attempts: 0s, 3s, 8s.
    """
    last_err: Optional[str] = None
    for attempt in range(attempts):
        if attempt:
            time.sleep(backoff[min(attempt, len(backoff) - 1)])
        try:
            resp = session.get(url, params=params, timeout=timeout)
        except Exception as e:  # noqa: BLE001 — network layer can raise various
            last_err = f"network error: {e}"
            log.info("  fetch_json attempt %d/%d failed (%s)", attempt + 1, attempts, last_err)
            continue
        status = resp.status_code
        if status >= 400 and status < 500:
            resp.raise_for_status()  # client error — not transient, surface it
        if status >= 500:
            last_err = f"status {status}"
            log.info("  fetch_json attempt %d/%d got %s, retrying", attempt + 1, attempts, last_err)
            continue
        body = resp.text or ""
        if not body.strip():
            last_err = f"empty body (status {status})"
            log.info("  fetch_json attempt %d/%d got empty body, retrying", attempt + 1, attempts)
            continue
        try:
            return resp.json()
        except ValueError:
            last_err = f"non-JSON body (status {status}): {body[:120]!r}"
            log.info("  fetch_json attempt %d/%d got non-JSON, retrying", attempt + 1, attempts)
            continue
    raise RuntimeError(f"fetch_json failed after {attempts} attempts for {url}: {last_err}")


class Source(ABC):
    """A repost source — knows how to list, parse, track, and frame articles."""

    name: str  # class attribute; e.g. "p_articles"

    # Subclasses can override to skip cloudscraper for non-CF sites.
    use_cloudscraper: bool = True

    def __init__(self) -> None:
        self._session = None

    def session(self):
        """Return the source's HTTP session (requests-compatible)."""
        if self._session is None:
            self._session = self._make_session()
        return self._session

    def _make_session(self):
        """Build the source's HTTP session. Override for custom transport
        (e.g. curl_cffi for sites that block standard fingerprints)."""
        return make_scraper_session(use_cloudscraper=self.use_cloudscraper)

    def repost_skip_reason(self, article: "Article") -> Optional[str]:
        """Return a reason to SKIP reposting this article, or None to repost.

        Base default: never skip. Sources override to apply their own content
        policy (e.g. p-articles skips pieces it reposted from a third platform).
        The orchestrator calls this after fetch_article; when a reason is
        returned it skips the draft and advances state past the article.
        """
        return None

    # ----- auto-publish scheduling -----

    def publish_order_key(self, ref: ArticleRef) -> Any:
        """Sort key for publishing oldest-first. Override per source.

        Default 0 keeps listing order; sources back this with their numeric id
        (p-articles numeric_id, WordPress wp_id) so older articles publish first.
        """
        return 0

    def publish_schedule(self, count: int, now_utc: datetime) -> list[Optional[str]]:
        """Given `count` articles (already oldest-first), return a per-article
        disposition aligned by index:

        - PUBLISH_NOW  -> publish immediately
        - <ISO string> -> schedule via Matters publishAt at that UTC time
        - None         -> leave as a draft (don't publish)

        Default policy (used by 法庭線): publish 2 immediately, then each further
        pair 12 minutes later — honours Matters' "2 publishes / 12 min" cap
        without sleeping the runner. Sources override for richer schedules.
        """
        out: list[Optional[str]] = []
        for i in range(count):
            pair = i // 2
            out.append(PUBLISH_NOW if pair == 0 else iso_utc(now_utc + timedelta(minutes=12 * pair)))
        return out

    @abstractmethod
    def list_recent_article_refs(self) -> list[ArticleRef]:
        """Return refs visible on the source's listing/homepage right now."""

    @abstractmethod
    def fetch_article(self, ref: ArticleRef) -> Article:
        """Fetch and parse a single article."""

    @abstractmethod
    def is_new(self, ref: ArticleRef, state: dict) -> bool:
        """Return True if this ref hasn't been seen yet according to `state`."""

    @abstractmethod
    def advance_state(self, state: dict, article: Article) -> None:
        """Mutate `state` to mark this article as seen."""

    @abstractmethod
    def bootstrap_state(self, refs: list[ArticleRef]) -> dict:
        """Build the initial state from a snapshot of currently-visible refs.

        Used on first run: everything currently listed is marked seen so we
        don't backfill old articles. Future runs only act on truly new refs.
        """

    @abstractmethod
    def build_header_html(self, article: Article) -> str:
        """HTML inserted above the article body in the Matters draft."""

    @abstractmethod
    def build_credit_html(self, article: Article) -> str:
        """HTML inserted below the article body in the Matters draft."""
