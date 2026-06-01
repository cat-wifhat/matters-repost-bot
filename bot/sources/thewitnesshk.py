"""Source: thewitnesshk.com (法庭線).

WordPress site with REST API enabled. Filters to the 焦點 (category id 28)
section per @mattershkrec's editorial preference.

The site's WAF returns hard 403s to Chrome-fingerprint sessions from
datacenter IP ranges, so we use curl_cffi's safari17_0 impersonation instead
of cloudscraper.

Body cleanup notes:
- The site lazy-loads images: the `src` attribute is a placeholder SVG and
  the real URL is in `srcset` (or sometimes `data-src`). We extract the
  largest URL from srcset.
- YouTube/iframe embeds inside `<figure class="wp-block-embed">` won't
  render on Matters — strip the whole figure.
"""
from __future__ import annotations

import logging
import re
from html import escape
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from .base import Article, ArticleRef, Source, make_curl_cffi_session

log = logging.getLogger(__name__)

SITE = "https://thewitnesshk.com"
API = f"{SITE}/wp-json/wp/v2"

# 焦點 — the section we mirror.
FOCUS_CATEGORY_ID = 28

CREDIT_LINKS = [
    ("法庭線官網",      "https://thewitnesshk.com/"),
    ("法庭線Facebook", "https://www.facebook.com/thewitnesshk"),
    ("法庭線YouTube",  "https://www.youtube.com/@thewitnesshk"),
    ("法庭線Instagram", "https://instagram.com/thewitnesshk"),
    ("法庭線Patreon",  "https://www.patreon.com/thewitnesshk"),
]

ALLOWED_TAGS = {
    "p", "br", "hr",
    "h2", "h3", "h4", "h5",
    "ul", "ol", "li",
    "blockquote",
    "strong", "em", "b", "i", "u",
    "a", "img",
}


class TheWitnessHkSource(Source):
    name = "thewitnesshk"

    def _make_session(self):
        # WAF hard-blocks Chrome TLS from datacenter IPs; Safari fingerprint
        # gets through.
        return make_curl_cffi_session(impersonate="safari17_0")

    # ----- listing & fetching -----

    def list_recent_article_refs(self) -> list[ArticleRef]:
        resp = self.session().get(
            f"{API}/posts",
            params={"categories": FOCUS_CATEGORY_ID,
                    "per_page": 20,
                    "_fields": "id,date,link"},
            timeout=30,
        )
        resp.raise_for_status()
        out: list[ArticleRef] = []
        for p in resp.json():
            pid = int(p["id"])
            out.append(ArticleRef(
                source=self.name,
                article_id=str(pid),
                url=p["link"],
                extra={"wp_id": pid, "date": (p.get("date") or "")[:10]},
            ))
        return out

    def fetch_article(self, ref: ArticleRef) -> Article:
        resp = self.session().get(
            f"{API}/posts/{ref.extra['wp_id']}",
            params={"_embed": "1"},
            timeout=30,
        )
        resp.raise_for_status()
        d = resp.json()

        title = d.get("title", {}).get("rendered", "").strip()
        if not title:
            raise ValueError(f"No title for post {ref.article_id}")
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
        return f'<p>（<a href="{escape(article.url)}">原文刊載於法庭線</a>）</p>'

    def build_credit_html(self, article: Article) -> str:
        return "".join(
            f'<p><a href="{escape(url)}">{escape(label)}</a></p>'
            for label, url in CREDIT_LINKS
        )


# ----- body cleaner -----

def _largest_from_srcset(srcset: str) -> str:
    """Pick the URL with the largest width descriptor from a srcset value."""
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

    # Drop scripts/styles/iframes (incl. YouTube embeds).
    for bad in root.find_all(["script", "style", "noscript", "iframe"]):
        bad.decompose()

    # Strip HTML comments (e.g. <!--more-->, VideographyWP plugin notices) —
    # otherwise they survive into the body as raw text after BS4 serialises.
    from bs4 import Comment
    for c in list(root.find_all(string=lambda s: isinstance(s, Comment))):
        c.extract()

    # Convert figcaption to its own paragraph so it survives <figure> unwrap.
    for cap in root.find_all("figcaption"):
        text = cap.get_text(" ", strip=True)
        if text:
            p = soup.new_tag("p")
            p.string = text
            cap.replace_with(p)
        else:
            cap.decompose()

    # Unwrap anchors wrapping only an image (WP click-to-zoom pattern).
    for a in root.find_all("a"):
        kids = [c for c in a.children if not (isinstance(c, str) and not c.strip())]
        if len(kids) == 1 and isinstance(kids[0], Tag) and kids[0].name == "img":
            a.unwrap()

    # Fix lazy-loaded images: real URL is in srcset or data-src, src is a
    # data:image/svg placeholder.
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

    # Resolve anchors and strip non-href attrs.
    for a in root.find_all("a"):
        href = (a.get("href") or "").strip()
        if href:
            a["href"] = urljoin(SITE + "/", href)
        for attr in list(a.attrs):
            if attr != "href":
                del a[attr]

    # Strip class/id/style/data-* from kept tags.
    for tag in root.find_all(True):
        if tag.name in ("img", "a"):
            continue
        for attr in list(tag.attrs):
            if attr in ("class", "id", "style") or attr.startswith("data-"):
                del tag[attr]

    # Unwrap non-allowed tags (figure, picture, section, div, span, ...).
    for tag in list(root.descendants):
        if isinstance(tag, Tag) and tag.name not in ALLOWED_TAGS:
            tag.unwrap()

    # Drop empty paragraphs.
    for p in root.find_all("p"):
        if not p.get_text(strip=True) and not p.find("img"):
            p.decompose()

    return "".join(str(c) for c in root.children).strip()
