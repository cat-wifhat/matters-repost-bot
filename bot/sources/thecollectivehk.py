"""Source: thecollectivehk.com (集誌社).

WordPress site. Filters to the 深度 section (category id 5, slug=in-depth)
per @mattershkrec's editorial preference — same destination Matters account
as 法庭線, just a separate scheduling/state stream.

WAF behaviour mirrors 法庭線: Chrome TLS from datacenters returns 403, so
we use curl_cffi safari17_0 impersonation. Body cleanup mirrors the witness
source — lazy images, iframe embeds, WP block clutter.
"""
from __future__ import annotations

import logging
import re
from html import escape
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from .base import Article, ArticleRef, Source, fetch_json, make_curl_cffi_session

log = logging.getLogger(__name__)

SITE = "https://thecollectivehk.com"
API = f"{SITE}/wp-json/wp/v2"

# 深度 — the section we mirror.
IN_DEPTH_CATEGORY_ID = 5

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

    def _make_session(self):
        return make_curl_cffi_session(impersonate="safari17_0")

    # ----- listing & fetching -----

    def list_recent_article_refs(self) -> list[ArticleRef]:
        posts = fetch_json(
            self.session(),
            f"{API}/posts",
            params={"categories": IN_DEPTH_CATEGORY_ID,
                    "per_page": 20,
                    "_fields": "id,date,link"},
        )
        out: list[ArticleRef] = []
        for p in posts:
            pid = int(p["id"])
            out.append(ArticleRef(
                source=self.name,
                article_id=str(pid),
                url=p["link"],
                extra={"wp_id": pid, "date": (p.get("date") or "")[:10]},
            ))
        return out

    def fetch_article(self, ref: ArticleRef) -> Article:
        d = fetch_json(
            self.session(),
            f"{API}/posts/{ref.extra['wp_id']}",
            params={"_embed": "1"},
        )

        title = d.get("title", {}).get("rendered", "").strip()
        if not title:
            raise ValueError(f"No title for post {ref.article_id}")
        # @mattershkrec receives drafts from multiple sources — prefix the
        # source label so editors can tell them apart in the drafts list.
        title = f"【集誌社】{title}"
        date = (d.get("date") or "")[:10]
        content_html = d.get("content", {}).get("rendered", "")

        embedded = d.get("_embedded", {}) or {}
        authors = embedded.get("author", []) or []
        author = (authors[0].get("name") if authors else "") or ""

        featured_images: list[str] = []
        for m in embedded.get("wp:featuredmedia", []) or []:
            src = (m or {}).get("source_url")
            if src:
                featured_images.append(src)

        tags: list[str] = []
        for term_group in embedded.get("wp:term", []) or []:
            for term in term_group or []:
                if term.get("taxonomy") == "post_tag":
                    name = term.get("name")
                    if name and name not in tags:
                        tags.append(name)

        body_html = _clean_body(content_html)

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
            extra={"wp_id": ref.extra["wp_id"]},
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
