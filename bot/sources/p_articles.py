"""Source: p-articles.com (虛詞・無形)."""
from __future__ import annotations

import logging
import re
from html import escape
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from .base import Article, ArticleRef, Source

log = logging.getLogger(__name__)

SOURCE_BASE = "https://p-articles.com"

# Article links on the homepage are /<category>/<numeric_id>.html
ARTICLE_URL_RE = re.compile(r"^/([a-z_]+)/(\d+)\.html$")

CREDIT_LINKS = [
    ("虛詞・無形網站",     "https://p-articles.com/"),
    ("虛詞・無形Facebook", "https://www.facebook.com/formless.particles"),
    ("虛詞・無形YouTube",  "https://www.youtube.com/@formless.particles"),
    ("虛詞・無形Patreon",  "https://www.patreon.com/thehouseofhk_literature"),
]

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


class PArticlesSource(Source):
    name = "p_articles"
    use_cloudscraper = True   # p-articles sits behind Cloudflare

    # ----- listing & fetching -----

    def list_recent_article_refs(self) -> list[ArticleRef]:
        resp = self.session().get(SOURCE_BASE + "/", timeout=30)
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
                source=self.name,
                article_id=f"{category}/{sid}",
                url=urljoin(SOURCE_BASE + "/", a["href"]),
                extra={"category": category, "numeric_id": sid},
            )
        return list(seen.values())

    def fetch_article(self, ref: ArticleRef) -> Article:
        resp = self.session().get(ref.url, timeout=30)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        soup = BeautifulSoup(resp.text, "lxml")

        title = _text(soup.select_one(".detail_title_01 h1"))
        if not title:
            raise ValueError(f"No title found at {ref.url}")

        category_label, author, date = _parse_meta(
            soup.select_one(".detail_title_02 h4")
        )

        # p-articles is inconsistent about tags: some articles have one tag per
        # <a>, others cram several into a single <a> separated by spaces and
        # `#`. Split & dedupe so Matters doesn't reject as "bad tag format".
        tags: list[str] = []
        seen_tags: set[str] = set()
        for a in soup.select(".detail_title_02 p.tags a"):
            raw = _text(a)
            if not raw:
                continue
            for piece in re.split(r"[#\s]+", raw):
                piece = piece.strip()
                if piece and piece not in seen_tags:
                    seen_tags.add(piece)
                    tags.append(piece)

        featured_images: list[str] = []
        for img in soup.select("#image-gallery img"):
            src = (img.get("src") or "").strip()
            if src:
                featured_images.append(urljoin(SOURCE_BASE + "/", src))

        body_node = soup.select_one(".detail_contect_01")
        body_html = _clean_body(body_node) if body_node else ""

        return Article(
            source=self.name,
            article_id=ref.article_id,
            url=ref.url,
            title=title,
            author=author,
            date=date,
            tags=tags,
            featured_images=featured_images,
            body_html=body_html,
            extra={
                "category": ref.extra.get("category"),
                "category_label": category_label,
                "numeric_id": ref.extra.get("numeric_id"),
            },
        )

    # ----- state tracking -----

    def is_new(self, ref: ArticleRef, state: dict) -> bool:
        last_seen = state.get("last_seen_ids", {})
        category = ref.extra["category"]
        numeric_id = ref.extra["numeric_id"]
        return numeric_id > last_seen.get(category, 0)

    def advance_state(self, state: dict, article: Article) -> None:
        last_seen = state.setdefault("last_seen_ids", {})
        category = article.extra["category"]
        numeric_id = int(article.extra["numeric_id"])
        last_seen[category] = max(last_seen.get(category, 0), numeric_id)

    def bootstrap_state(self, refs: list[ArticleRef]) -> dict:
        last_seen: dict[str, int] = {}
        for r in refs:
            cat = r.extra["category"]
            nid = r.extra["numeric_id"]
            last_seen[cat] = max(last_seen.get(cat, 0), nid)
        return {"last_seen_ids": last_seen}

    # ----- header & credit -----

    def build_header_html(self, article: Article) -> str:
        parts = [
            f'<p>（<a href="{escape(article.url)}">原文刊載於虛詞・無形</a>）</p>'
        ]
        if article.author:
            parts.append(f"<p>文｜{escape(article.author)}</p>")
        return "".join(parts)

    def build_credit_html(self, article: Article) -> str:
        return "".join(
            f'<p><a href="{escape(url)}">{escape(label)}</a></p>'
            for label, url in CREDIT_LINKS
        )


# ----- helpers (private) -----

def _text(node) -> str:
    return node.get_text(strip=True) if node else ""


def _parse_meta(h4: Optional[Tag]) -> tuple[str, str, str]:
    """Parse '<a>影評</a> | by  王植 | 2026-05-27' → ('影評', '王植', '2026-05-27')."""
    if not h4:
        return "", "", ""
    raw = h4.get_text(" ", strip=True)
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
    """Matters-friendly HTML for the article body."""
    for bad in root.find_all(["script", "style", "noscript", "iframe"]):
        bad.decompose()

    for img in root.find_all("img"):
        src = (img.get("src") or "").strip()
        if not src:
            img.decompose()
            continue
        img["src"] = urljoin(SOURCE_BASE + "/", src)
        for attr in list(img.attrs):
            if attr not in ("src", "alt"):
                del img[attr]

    for a in root.find_all("a"):
        href = (a.get("href") or "").strip()
        if href:
            a["href"] = urljoin(SOURCE_BASE + "/", href)
        for attr in list(a.attrs):
            if attr != "href":
                del a[attr]

    for tag in list(root.descendants):
        if isinstance(tag, Tag) and tag.name not in ALLOWED_TAGS:
            tag.unwrap()

    for p in root.find_all("p"):
        if not p.get_text(strip=True) and not p.find("img"):
            p.decompose()

    return "".join(str(c) for c in root.children).strip()
