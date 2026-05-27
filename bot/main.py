"""Orchestrator: fetch new articles from p-articles, repost to Matters as drafts."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from html import escape
from pathlib import Path
from typing import Optional

from . import config
from .matters_client import MattersClient, MattersError
from .scraper import (
    Article,
    ArticleRef,
    fetch_article,
    list_recent_article_refs,
)

log = logging.getLogger("repost")


def load_state(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"last_seen_ids": {}, "history": []}
    return json.loads(p.read_text(encoding="utf-8"))


def save_state(path: str, state: dict) -> None:
    Path(path).write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def select_new_refs(
    all_refs: list[ArticleRef],
    last_seen: dict[str, int],
) -> list[ArticleRef]:
    """Return refs whose ID is greater than the last seen ID for their category.

    Sorted oldest-first so reposts land in chronological order.
    """
    new = [r for r in all_refs if r.article_id > last_seen.get(r.category, 0)]
    new.sort(key=lambda r: (r.category, r.article_id))
    return new


def bootstrap_state(all_refs: list[ArticleRef]) -> dict[str, int]:
    """First-run state: record the current max ID per category so the next run
    only picks up genuinely new articles. No reposting happens on bootstrap."""
    out: dict[str, int] = {}
    for r in all_refs:
        out[r.category] = max(out.get(r.category, 0), r.article_id)
    return out


def build_credit_html(article: Article) -> str:
    """Trailing block: source link, author credit, and 虛詞無形 social links."""
    lines = ["<hr>"]
    lines.append(
        f'<p>原文出處：<a href="{escape(article.url)}">'
        f'虛詞 p-articles —— {escape(article.title)}</a></p>'
    )
    if article.author:
        lines.append(f"<p>作者：{escape(article.author)}</p>")
    meta_bits = []
    if article.category_label:
        meta_bits.append(f"分類：{escape(article.category_label)}")
    if article.date:
        meta_bits.append(f"原文日期：{escape(article.date)}")
    if meta_bits:
        lines.append(f"<p>{' ｜ '.join(meta_bits)}</p>")

    lines.append("<p>虛詞・無形：</p><ul>")
    for label, url in config.SOCIAL_LINKS.items():
        lines.append(f'<li>{escape(label)}：<a href="{escape(url)}">{escape(url)}</a></li>')
    lines.append("</ul>")
    return "".join(lines)


def build_featured_html(article: Article, image_path_by_src: dict[str, str]) -> str:
    """Render the top-of-page gallery as figures using uploaded Matters URLs."""
    out = []
    for src in article.featured_images:
        matters_url = image_path_by_src.get(src)
        if not matters_url:
            continue
        out.append(f'<figure><img src="{escape(matters_url)}"></figure>')
    return "".join(out)


def rewrite_body_images(body_html: str, image_path_by_src: dict[str, str]) -> str:
    """Replace each p-articles image src with the corresponding Matters URL.

    We do a string replace rather than re-parsing — body_html was already
    cleaned by the scraper, and srcs are absolute URLs so collisions are
    extremely unlikely.
    """
    out = body_html
    for src, matters_url in image_path_by_src.items():
        out = out.replace(f'src="{src}"', f'src="{matters_url}"')
    return out


def repost_article(
    client: MattersClient,
    article: Article,
    *,
    dry_run: bool = False,
    publish: bool = False,
) -> Optional[dict]:
    """Upload images, create draft, attach content. Returns the draft summary."""
    title = article.title

    if dry_run:
        log.info("[DRY-RUN] would repost: %s — %s", article.category, title)
        return None

    log.info("Creating empty draft: %s", title)
    draft_id = client.create_empty_draft(title=title)
    log.info("  draft_id=%s", draft_id)

    # Upload all images (featured + inline body images). Use the first featured
    # image (if any) as the cover.
    all_image_srcs = list(article.featured_images)
    for src in _extract_body_image_srcs(article.body_html):
        if src not in all_image_srcs:
            all_image_srcs.append(src)

    image_path_by_src: dict[str, str] = {}
    cover_asset_id: Optional[str] = None
    for idx, src in enumerate(all_image_srcs):
        asset_type = "cover" if idx == 0 else "embed"
        try:
            asset = client.upload_image_by_url(src, draft_id=draft_id, asset_type=asset_type)
        except MattersError as e:
            log.warning("  image upload failed for %s: %s", src, e)
            continue
        path = asset.get("path") or ""
        if path:
            image_path_by_src[src] = path
            log.info("  uploaded image (%s) → %s", asset_type, path)
        if idx == 0 and asset.get("id"):
            cover_asset_id = asset["id"]

    featured_html = build_featured_html(article, image_path_by_src)
    body_html = rewrite_body_images(article.body_html, image_path_by_src)
    credit_html = build_credit_html(article)
    full_content = featured_html + body_html + credit_html

    # Matters caps tags at 3 per article. p-articles routinely has 10+ — take
    # the first 3 (source ordering is roughly by relevance).
    tags = (article.tags or [])[:3]

    log.info("Updating draft with full content (%d chars, %d tags)",
             len(full_content), len(tags))
    result = client.update_draft(
        draft_id,
        title=title,
        content=full_content,
        tags=tags or None,
        cover_asset_id=cover_asset_id,
        license="arr",
    )

    if publish:
        log.info("Publishing draft %s", draft_id)
        result = client.publish_draft(draft_id)

    return result


def _extract_body_image_srcs(body_html: str) -> list[str]:
    """Cheap regex pass to find image srcs in the cleaned body."""
    import re
    return re.findall(r'<img[^>]+src="([^"]+)"', body_html)


def run(
    *,
    state_path: str,
    dry_run: bool,
    publish: bool,
    max_articles: int,
    bootstrap_only: bool = False,
) -> int:
    state = load_state(state_path)
    last_seen = dict(state.get("last_seen_ids") or {})

    log.info("Fetching homepage of %s ...", config.SOURCE_BASE)
    refs = list_recent_article_refs()
    log.info("Found %d article links on homepage", len(refs))

    if not last_seen or bootstrap_only:
        new_state_ids = bootstrap_state(refs)
        log.info("Bootstrapping state — recording current max IDs, posting nothing:")
        for cat, mid in sorted(new_state_ids.items()):
            log.info("  %s: %d", cat, mid)
        state["last_seen_ids"] = new_state_ids
        save_state(state_path, state)
        return 0

    new_refs = select_new_refs(refs, last_seen)
    log.info("New articles to repost: %d", len(new_refs))

    if not new_refs:
        return 0

    if len(new_refs) > max_articles:
        log.warning(
            "Capping run to MAX_ARTICLES_PER_RUN=%d (would have processed %d). "
            "Remaining articles will be picked up next run.",
            max_articles, len(new_refs),
        )
        new_refs = new_refs[:max_articles]

    client = None
    if not dry_run:
        if not config.MATTERS_EMAIL or not config.MATTERS_PASSWORD:
            log.error("MATTERS_EMAIL / MATTERS_PASSWORD not set. Aborting.")
            return 2
        client = MattersClient()
        client.login(config.MATTERS_EMAIL, config.MATTERS_PASSWORD)

    processed: list[dict] = []
    failures: list[dict] = []
    for ref in new_refs:
        try:
            log.info("---- %s/%s ----", ref.category, ref.article_id)
            art = fetch_article(ref)
            result = repost_article(client, art, dry_run=dry_run, publish=publish)
            processed.append({
                "category": ref.category,
                "article_id": ref.article_id,
                "title": art.title,
                "url": ref.url,
                "draft": result,
            })
            # Advance state only on success so retries pick failures up next run.
            last_seen[ref.category] = max(last_seen.get(ref.category, 0), ref.article_id)
            state["last_seen_ids"] = last_seen
            save_state(state_path, state)
            time.sleep(2)  # polite delay between articles
        except Exception as e:
            log.exception("Failed processing %s/%s: %s", ref.category, ref.article_id, e)
            failures.append({
                "category": ref.category,
                "article_id": ref.article_id,
                "url": ref.url,
                "error": str(e),
            })

    history_entry = {
        "ts": int(time.time()),
        "processed": processed,
        "failures": failures,
    }
    state.setdefault("history", []).append(history_entry)
    state["history"] = state["history"][-20:]  # cap history
    save_state(state_path, state)

    log.info("Done. %d processed, %d failed.", len(processed), len(failures))
    return 1 if failures else 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Repost p-articles to Matters.")
    parser.add_argument("--state", default=config.STATE_PATH, help="Path to state JSON.")
    parser.add_argument("--dry-run", action="store_true", help="Don't talk to Matters.")
    parser.add_argument(
        "--publish", action="store_true",
        help="Publish drafts immediately (default: leave as drafts for manual review).",
    )
    parser.add_argument(
        "--bootstrap", action="store_true",
        help="Record current max IDs without posting anything.",
    )
    parser.add_argument(
        "--max", type=int, default=config.MAX_ARTICLES_PER_RUN,
        help="Cap on articles processed per run.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    dry_run = args.dry_run or config.DRY_RUN
    publish = args.publish or config.PUBLISH

    return run(
        state_path=args.state,
        dry_run=dry_run,
        publish=publish,
        max_articles=args.max,
        bootstrap_only=args.bootstrap,
    )


if __name__ == "__main__":
    sys.exit(main())
