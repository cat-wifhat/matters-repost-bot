"""Base abstractions for repost sources.

Each source site (p-articles, newsmarket, foodthink, ...) implements a Source
subclass: how to list its recent articles, how to parse one, how to track
"already seen", and how to wrap the article with site-specific header/credit
HTML on Matters.

The orchestrator (bot/main.py) is source-agnostic and just calls these methods.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

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


class Source(ABC):
    """A repost source — knows how to list, parse, track, and frame articles."""

    name: str  # class attribute; e.g. "p_articles"

    # Subclasses can override to skip cloudscraper for non-CF sites.
    use_cloudscraper: bool = True

    def __init__(self) -> None:
        self._session: Optional[requests.Session] = None

    def session(self) -> requests.Session:
        if self._session is None:
            self._session = make_scraper_session(use_cloudscraper=self.use_cloudscraper)
        return self._session

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
