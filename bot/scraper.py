"""Scrape p-articles.com for new articles."""
import logging
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

from .config import SOURCE_BASE, USER_AGENT

log = logging.getLogger(__name__)

ARTICLE_URL_RE = re.compile(r"^/([a-z_]+)/(\d+)\.html$")

# Tags we keep in the article body. Anything else gets unwrapped (children kept,
# tag removed). Matters' editor is conservative — keep this list narrow.
ALLOWED_TAGS = {
    "p", "br", "hr",
    "h2", "h3", "h4",
    "ul", "ol", "li",
    "blockquote",
    "strong", "em", "b", "i", "u",
    "a", "img",
    "figure", "figcaption",
}


@dataclass
class ArticleRef:
    category: str
    article_id: int
    url: str


@dataclass
class Article:
    category: str          # url segment, e.g. "critics"
    category_label: str    # display label, e.g. "影評"
    article_id: int
    url: str
    title: str
    author: str
    date: str
    tags: list[str] = field(default_factory=list)
    featured_images: list[str] = field(default_factory=list)
    body_html: str = ""


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def list_recent_article_refs(session: Optional[requests.Session] = None) -> list[ArticleRef]:
    """Scan the p-articles homepage and return all article refs found there.

    Articles appear as links like /<category>/<id>.html on the homepage.
    Returns a de-duplicated list.
    """
    s = session or _session()
    resp = s.get(SOURCE_BASE + "/", timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    seen: dict[tuple[str, int], ArticleRef] = {}
    for a in soup.find_all("a", href=True):
        m = ARTICLE_URL_RE.match(a["href"])
        if not m:
            continue
        category, sid = m.group(1), int(m.group(2))
        key = (category, sid)
        if key in seen:
            continue
        seen[key] = ArticleRef(
            category=category,
            article_id=sid,
            url=urljoin(SOURCE_BASE + "/", a["href"]),
        )
    return list(seen.values())


def fetch_article(ref: ArticleRef, session: Optional[requests.Session] = None) -> Article:
    s = session or _session()
    resp = s.get(ref.url, timeout=30)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"
    soup = BeautifulSoup(resp.text, "lxml")

    title = _text(soup.select_one(".detail_title_01 h1"))
    if not title:
        raise ValueError(f"No title found at {ref.url}")

    meta_h4 = soup.select_one(".detail_title_02 h4")
    category_label, author, date = _parse_meta(meta_h4)

    tags = [_text(a) for a in soup.select(".detail_title_02 p.tags a") if _text(a)]

    featured_images = []
    for img in soup.select("#image-gallery img"):
        src = img.get("src", "").strip()
        if src:
            featured_images.append(urljoin(SOURCE_BASE + "/", src))

    body_node = soup.select_one(".detail_contect_01")
    body_html = _clean_body(body_node) if body_node else ""

    return Article(
        category=ref.category,
        category_label=category_label or ref.category,
        article_id=ref.article_id,
        url=ref.url,
        title=title,
        author=author,
        date=date,
        tags=tags,
        featured_images=featured_images,
        body_html=body_html,
    )


def _text(node) -> str:
    return node.get_text(strip=True) if node else ""


def _parse_meta(h4: Optional[Tag]) -> tuple[str, str, str]:
    """Parse '<a>影評</a> | by  王植 | 2026-05-27' → ('影評', '王植', '2026-05-27')."""
    if not h4:
        return "", "", ""
    raw = h4.get_text(" ", strip=True)
    # Strip HTML entities that BeautifulSoup may have left as text.
    parts = [p.strip() for p in re.split(r"[|｜]", raw) if p.strip()]
    category_label = parts[0] if parts else ""
    author = ""
    date = ""
    for p in parts[1:]:
        m = re.match(r"by\s*(.+)", p, re.I)
        if m:
            author = m.group(1).strip()
            continue
        if re.match(r"\d{4}-\d{1,2}-\d{1,2}", p):
            date = p
    return category_label, author, date


def _clean_body(root: Tag) -> str:
    """Return a Matters-friendly HTML string for the article body.

    Resolves relative image URLs, drops scripts/styles/comments, and unwraps tags
    outside ALLOWED_TAGS while keeping their text content.
    """
    # Drop scripts, styles, and HTML comments outright.
    for bad in root.find_all(["script", "style", "noscript", "iframe"]):
        bad.decompose()

    # Resolve image src to absolute, strip data- attributes.
    for img in root.find_all("img"):
        src = (img.get("src") or "").strip()
        if not src:
            img.decompose()
            continue
        img["src"] = urljoin(SOURCE_BASE + "/", src)
        # Keep only src and alt; Matters ignores the rest.
        for attr in list(img.attrs):
            if attr not in ("src", "alt"):
                del img[attr]

    # Resolve anchor hrefs to absolute.
    for a in root.find_all("a"):
        href = (a.get("href") or "").strip()
        if href:
            a["href"] = urljoin(SOURCE_BASE + "/", href)
        for attr in list(a.attrs):
            if attr not in ("href",):
                del a[attr]

    # Unwrap disallowed tags (keep children).
    for tag in list(root.descendants):
        if isinstance(tag, Tag) and tag.name not in ALLOWED_TAGS:
            tag.unwrap()

    # Collapse empty paragraphs (e.g. <p><br></p> stacks).
    for p in root.find_all("p"):
        if not p.get_text(strip=True) and not p.find("img"):
            p.decompose()

    # Return the inner HTML of the body container.
    return "".join(str(c) for c in root.children).strip()
