"""Source: thecollectivehk.com (集誌社).

WordPress site. Mirrors the 深度 (in-depth) section per @mattershkrec's
editorial preference — same destination Matters account as 法庭線, just a
separate scheduling/state stream.

Fetch strategy — RSS feed, not the REST API. This host runs SiteGround's
"Security Optimizer", which serves an `sgcaptcha` interstitial to datacenter
IPs (GitHub Actions runners) hitting `/wp-json/` — the old REST path, so every
scheduled run failed at listing. The category RSS feed
(`/category/in-depth/feed/`) is treated as ordinary crawlable content and isn't
challenged, and it already carries the FULL post body (`content:encoded`) plus
title/link/author/date/tags — so one feed GET replaces the old list + per-post
REST fetches entirely. curl_cffi safari17_0 impersonation is kept as belt-and-
braces. Body cleanup is unchanged. Note: the feed exposes the 10 most recent
in-depth posts; 深度 publishes far fewer than 10 between runs, and the
orchestrator caps each run at 10 anyway, so the window is not a constraint.
"""
from __future__ import annotations

import logging
import re
from email.utils import parsedate_to_datetime
from html import escape
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup, Tag

from .base import Article, ArticleRef, Source, fetch_text, make_curl_cffi_session

log = logging.getLogger(__name__)

SITE = "https://thecollectivehk.com"
FEED = f"{SITE}/category/in-depth/feed/"

# 深度 — the section we mirror. It's the feed's own category, so it shows up on
# every item's <category> list; drop it when collecting tags.
SECTION_LABEL = "深度"

# RSS module namespaces used by WordPress feeds.
RSS_NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
}

CREDIT_LINKS = [
    ("集誌社官網",      "https://thecollectivehk.com/"),
    ("集誌社Facebook", "https://www.facebook.com/thecollectivehongkong"),
    ("集誌社Podcast",  "https://open.spotify.com/show/1VRgcHrohHpfTIsMy8qvE6"),
    ("集誌社Instagram", "https://www.instagram.com/the_collectivehk/"),
    ("集誌社Patreon",  "https://www.patreon.com/thecollectivehk"),
]

ALLOWED_TAGS = {
    "p", "br", "hr",
    "h2", "h3", "h4", "h5",
    "ul", "ol", "li",
    "blockquote",
    "strong", "em", "b", "i", "u",
    "a", "img",
}


class TheCollectiveHkSource(Source):
    name = "thecollectivehk"

    def __init__(self) -> None:
        super().__init__()
        # Cache of feed items keyed by wp_id, populated by list_recent_article_refs
        # so fetch_article doesn't re-request — the feed already has full bodies.
        self._items_by_id: dict[int, dict] = {}

    def _make_session(self):
        return make_curl_cffi_session(impersonate="safari17_0")

    # ----- listing & fetching -----

    def _load_feed(self) -> dict[int, dict]:
        xml = fetch_text(self.session(), FEED)
        items = _parse_feed(xml)
        self._items_by_id = {it["wp_id"]: it for it in items}
        return self._items_by_id

    def list_recent_article_refs(self) -> list[ArticleRef]:
        items = self._load_feed()
        out: list[ArticleRef] = []
        for it in items.values():
            out.append(ArticleRef(
                source=self.name,
                article_id=str(it["wp_id"]),
                url=it["link"],
                extra={"wp_id": it["wp_id"], "date": it["date"]},
            ))
        return out

    def fetch_article(self, ref: ArticleRef) -> Article:
        wp_id = ref.extra["wp_id"]
        it = self._items_by_id.get(wp_id)
        if it is None:
            # Feed shifted between list and fetch (or a fresh instance) — reload.
            it = self._load_feed().get(wp_id)
        if it is None:
            raise ValueError(f"post {ref.article_id} not present in feed")

        title = it["title"]
        if not title:
            raise ValueError(f"No title for post {ref.article_id}")
        # @mattershkrec receives drafts from multiple sources — prefix the
        # source label so editors can tell them apart in the drafts list.
        title = f"【集誌社】{title}"

        body_html = _clean_body(it["content"])

        # The feed has no featured-media field; the lead image is the first one
        # in the body, which the orchestrator already uses as the cover.
        return Article(
            source=self.name,
            article_id=ref.article_id,
            url=ref.url,
            title=title,
            author=it["author"],
            date=it["date"],
            tags=it["tags"],
            featured_images=[],
            body_html=body_html,
            extra={"wp_id": wp_id},
        )

    # ----- state tracking -----

    def is_new(self, ref: ArticleRef, state: dict) -> bool:
        return ref.extra["wp_id"] > int(state.get("last_seen_id", 0))

    def advance_state(self, state: dict, article: Article) -> None:
        wp_id = int(article.extra["wp_id"])
        state["last_seen_id"] = max(int(state.get("last_seen_id", 0)), wp_id)

    def bootstrap_state(self, refs: list[ArticleRef]) -> dict:
        return {"last_seen_id": max((r.extra["wp_id"] for r in refs), default=0)}

    # ----- header & credit -----

    def build_header_html(self, article: Article) -> str:
        return f'<p>（<a href="{escape(article.url)}">原文刊載於集誌社</a>）</p>'

    def build_credit_html(self, article: Article) -> str:
        return "".join(
            f'<p><a href="{escape(url)}">{escape(label)}</a></p>'
            for label, url in CREDIT_LINKS
        )


# ----- RSS feed parsing -----

def _rss_date(pubdate: str) -> str:
    """RFC-822 pubDate (e.g. 'Thu, 04 Jun 2026 18:44:52 +0000') -> 'YYYY-MM-DD'."""
    if not pubdate:
        return ""
    try:
        dt = parsedate_to_datetime(pubdate)
        return dt.date().isoformat() if dt else ""
    except (TypeError, ValueError):
        return ""


def _parse_feed(xml: str) -> list[dict]:
    """Parse a WordPress category RSS feed into a list of post dicts.

    Each dict: {wp_id, title, link, author, date, tags, content}. Items whose
    guid carries no numeric post id are skipped (we key state on wp_id).
    """
    root = ET.fromstring(xml)
    out: list[dict] = []
    for item in root.findall("./channel/item"):
        guid = (item.findtext("guid") or "").strip()
        m = re.search(r"[?&]p=(\d+)", guid)
        if not m:
            continue
        wp_id = int(m.group(1))

        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        author = (item.findtext("dc:creator", default="", namespaces=RSS_NS) or "").strip()
        date = _rss_date(item.findtext("pubDate") or "")

        tags: list[str] = []
        for c in item.findall("category"):
            name = (c.text or "").strip()
            if name and name != SECTION_LABEL and name not in tags:
                tags.append(name)

        content = item.findtext("content:encoded", default="", namespaces=RSS_NS) or ""

        out.append({
            "wp_id": wp_id,
            "title": title,
            "link": link,
            "author": author,
            "date": date,
            "tags": tags,
            "content": content,
        })
    return out


# ----- body cleaner (same shape as thewitnesshk) -----

def _largest_from_srcset(srcset: str) -> str:
    best_url = ""
    best_w = -1
    for chunk in srcset.split(","):
        parts = chunk.strip().split()
        if not parts:
            continue
        url = parts[0]
        w = -1
        for p in parts[1:]:
            m = re.match(r"(\d+)w$", p)
            if m:
                w = int(m.group(1))
                break
        if w > best_w:
            best_w = w
            best_url = url
    return best_url


def _clean_body(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    root = soup.body or soup

    for bad in root.find_all(["script", "style", "noscript", "iframe"]):
        bad.decompose()

    from bs4 import Comment
    for c in list(root.find_all(string=lambda s: isinstance(s, Comment))):
        c.extract()

    for cap in root.find_all("figcaption"):
        text = cap.get_text(" ", strip=True)
        if text:
            p = soup.new_tag("p")
            p.string = text
            cap.replace_with(p)
        else:
            cap.decompose()

    for a in root.find_all("a"):
        kids = [c for c in a.children if not (isinstance(c, str) and not c.strip())]
        if len(kids) == 1 and isinstance(kids[0], Tag) and kids[0].name == "img":
            a.unwrap()

    for img in root.find_all("img"):
        src = (img.get("src") or "").strip()
        data_src = (img.get("data-src") or "").strip()
        srcset = (img.get("srcset") or img.get("data-srcset") or "").strip()
        real = ""
        if data_src and not data_src.startswith("data:"):
            real = data_src
        elif src and not src.startswith("data:"):
            real = src
        elif srcset:
            real = _largest_from_srcset(srcset)
        if not real:
            img.decompose()
            continue
        img["src"] = urljoin(SITE + "/", real)
        for attr in list(img.attrs):
            if attr not in ("src", "alt"):
                del img[attr]

    for a in root.find_all("a"):
        href = (a.get("href") or "").strip()
        if href:
            a["href"] = urljoin(SITE + "/", href)
        for attr in list(a.attrs):
            if attr != "href":
                del a[attr]

    for tag in root.find_all(True):
        if tag.name in ("img", "a"):
            continue
        for attr in list(tag.attrs):
            if attr in ("class", "id", "style") or attr.startswith("data-"):
                del tag[attr]

    for tag in list(root.descendants):
        if isinstance(tag, Tag) and tag.name not in ALLOWED_TAGS:
            tag.unwrap()

    for p in root.find_all("p"):
        if not p.get_text(strip=True) and not p.find("img"):
            p.decompose()

    return "".join(str(c) for c in root.children).strip()
