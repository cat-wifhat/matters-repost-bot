"""Orchestrator: pull from a named source, repost to Matters as drafts."""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Optional

from . import config
from .matters_client import MattersClient, MattersError
from .sources import (
    PUBLISH_NOW, Article, Source, fetch_image_bytes, get_source, iso_utc,
    known_sources,
)

log = logging.getLogger("repost")


# ---- state ----

def load_state(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def save_state(path: str, state: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


# ---- publish queue (drip publishing, no Matters native scheduling) ----
#
# Matters rate-limits publishArticle (2 / 12 min) — and that cap also blocks its
# native scheduling (publishAt), even from the editor. So instead of asking
# Matters to schedule, the creation run drops "publish later" items into this
# queue, and a separate workflow fires at each slot time (Tue/Wed/Fri/Sat/Sun
# 09/10/11 HKT) to publish whatever is due — one article per run, never near the
# rate limit. Each item: {"draft_id", "publish_at" (ISO UTC), "title"}.

def queue_path_for(state_path: str) -> str:
    p = Path(state_path)
    return str(p.with_name(p.stem + "_queue.json"))


def load_queue(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def save_queue(path: str, queue: list[dict]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(queue, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---- content composition ----

def _build_featured_html(
    article: Article,
    image_path_by_src: dict[str, str],
    asset_id_by_src: dict[str, str],
) -> str:
    """Render featured images as <figure class="image"> blocks at the top.

    Matters' parser crashes (`Cannot read properties of undefined ('firstChild')`)
    unless each figure has BOTH a self-closing <img/> with data-asset-id AND an
    empty <figcaption>.
    """
    out = []
    for src in article.featured_images:
        url = image_path_by_src.get(src)
        if not url:
            continue
        asset_id = asset_id_by_src.get(src, "")
        out.append(
            f'<figure class="image">'
            f'<img src="{escape(url)}" data-asset-id="{escape(asset_id)}" />'
            f'<figcaption></figcaption>'
            f'</figure>'
        )
    return "".join(out)


def _rewrite_body_images(body_html: str, image_path_by_src: dict[str, str]) -> str:
    """Swap source image URLs in body HTML for the uploaded Matters URLs."""
    out = body_html
    for src, url in image_path_by_src.items():
        out = out.replace(f'src="{src}"', f'src="{url}"')
    return out


def _extract_body_image_srcs(body_html: str) -> list[str]:
    return re.findall(r'<img[^>]+src="([^"]+)"', body_html)


# ---- repost one article ----

def create_filled_draft(
    client: MattersClient,
    source: Source,
    article: Article,
) -> str:
    """Create a Matters draft, upload images, and fill the full content.

    Returns the draft id. Whether/when to publish is decided by the caller
    (immediate, scheduled via publishAt, or left as a draft).
    """
    log.info("Creating empty draft: %s", article.title)
    draft_id = client.create_empty_draft(title=article.title)
    log.info("  draft_id=%s", draft_id)

    # Aggregate all images: featured + any inline in the body.
    all_image_srcs = list(article.featured_images)
    for src in _extract_body_image_srcs(article.body_html):
        if src not in all_image_srcs:
            all_image_srcs.append(src)

    image_path_by_src: dict[str, str] = {}
    image_asset_id_by_src: dict[str, str] = {}
    image_bytes_cache: dict[str, tuple[bytes, str]] = {}
    cover_asset_id: Optional[str] = None

    # Upload as 'embed' (gives a body-usable URL). Cover is uploaded separately.
    for src in all_image_srcs:
        try:
            content, mime = fetch_image_bytes(src, session=source.session())
            image_bytes_cache[src] = (content, mime)
            filename = src.rsplit("/", 1)[-1] or "image.png"
            asset = client.upload_image_file(
                content, filename, mime, draft_id=draft_id, asset_type="embed",
            )
            log.info("  embed asset: id=%s path=%s (%d bytes %s)",
                     asset.get("id"), asset.get("path"), len(content), mime)
            path = asset.get("path") or ""
            if path:
                image_path_by_src[src] = path
                image_asset_id_by_src[src] = asset.get("id") or ""
        except Exception as e:
            log.warning("  embed upload failed for %s: %s", src, e)

    if all_image_srcs:
        first_src = all_image_srcs[0]
        try:
            content, mime = (
                image_bytes_cache.get(first_src)
                or fetch_image_bytes(first_src, session=source.session())
            )
            filename = first_src.rsplit("/", 1)[-1] or "cover.png"
            cover_asset = client.upload_image_file(
                content, filename, mime, draft_id=draft_id, asset_type="cover",
            )
            cover_asset_id = cover_asset.get("id")
            log.info("  cover asset: id=%s path=%s",
                     cover_asset_id, cover_asset.get("path"))
        except Exception as e:
            log.warning("  cover upload failed for %s: %s", first_src, e)

    header_html = source.build_header_html(article)
    featured_html = _build_featured_html(article, image_path_by_src, image_asset_id_by_src)
    body_html = _rewrite_body_images(article.body_html, image_path_by_src)
    credit_html = source.build_credit_html(article)
    full_content = header_html + featured_html + body_html + credit_html

    # Matters caps tags at 3; sources may return more.
    tags = (article.tags or [])[:3]

    log.info("Updating draft with full content (%d chars, %d tags)",
             len(full_content), len(tags))
    client.update_draft(
        draft_id,
        title=article.title,
        content=full_content,
        tags=tags or None,
        cover_asset_id=cover_asset_id,
        license="arr",
    )

    return draft_id


# ---- main loop ----

def run(
    *,
    source_name: str,
    state_path: str,
    dry_run: bool,
    publish: bool,
    max_articles: int,
    bootstrap_only: bool,
) -> int:
    source = get_source(source_name)
    state = load_state(state_path)

    log.info("Source=%s state=%s", source_name, state_path)
    refs = source.list_recent_article_refs()
    log.info("Found %d article refs", len(refs))

    if not state or bootstrap_only:
        new_state = source.bootstrap_state(refs)
        log.info("Bootstrapping state — recording current refs as seen, posting nothing.")
        log.info("  state: %s", json.dumps(new_state, ensure_ascii=False))
        save_state(state_path, new_state)
        return 0

    new_refs = [r for r in refs if source.is_new(r, state)]
    # Publish oldest-first so the Matters timeline reads chronologically.
    new_refs.sort(key=source.publish_order_key)
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

    # When auto-publishing, decide each article's disposition (immediate /
    # queued for a slot / left as draft). Indexed by *published* position so
    # skipped articles don't consume a slot.
    now_utc = datetime.now(timezone.utc)
    dispositions = source.publish_schedule(len(new_refs), now_utc) if publish else []
    queue_path = queue_path_for(state_path)
    queue = load_queue(queue_path) if publish else []

    client: Optional[MattersClient] = None
    if not dry_run:
        if not config.MATTERS_EMAIL or not config.MATTERS_PASSWORD:
            log.error("MATTERS_EMAIL / MATTERS_PASSWORD not set. Aborting.")
            return 2
        client = MattersClient()
        client.login(config.MATTERS_EMAIL, config.MATTERS_PASSWORD)

    processed: list[dict] = []
    failures: list[dict] = []
    skipped: list[dict] = []
    publish_idx = 0
    for ref in new_refs:
        try:
            log.info("---- %s %s ----", source_name, ref.article_id)
            article = source.fetch_article(ref)

            # Content-policy filter (e.g. drop the source's own third-party
            # reposts). Mark as seen so we don't re-fetch it every run, but
            # never advance state in dry-run. Skips don't consume a publish slot.
            skip_reason = source.repost_skip_reason(article)
            if skip_reason:
                log.info("SKIP %s — %s: %s", ref.article_id, skip_reason, article.title)
                skipped.append({"article_id": ref.article_id, "title": article.title,
                                "url": ref.url, "reason": skip_reason})
                if not dry_run:
                    source.advance_state(state, article)
                    save_state(state_path, state)
                continue

            disp = dispositions[publish_idx] if publish and publish_idx < len(dispositions) else None
            if publish:
                publish_idx += 1
            plan = ("publish now" if disp == PUBLISH_NOW
                    else f"queue for {disp}" if disp
                    else ("leave as draft" if publish else "draft (no publish)"))

            if dry_run:
                log.info("[DRY-RUN] %s — %s", plan, article.title)
                processed.append({"article_id": ref.article_id, "title": article.title,
                                  "url": ref.url, "plan": plan})
                continue

            draft_id = create_filled_draft(client, source, article)
            if publish and disp == PUBLISH_NOW:
                # Immediate publishes are ≤2 per run — within the rate limit.
                try:
                    client.publish_draft(draft_id)
                    log.info("  published now")
                except MattersError as e:
                    log.warning("Publish failed (left as draft): %s", e)
            elif publish and disp:
                # Queue for the drip workflow to publish at its slot time. We do
                # NOT use Matters' publishAt (its scheduling is rate-limited too).
                queue.append({"draft_id": draft_id, "publish_at": disp,
                              "title": article.title})
                save_queue(queue_path, queue)
                log.info("  queued for %s", disp)
            elif publish:
                log.info("  left as draft (beyond schedule capacity)")
            processed.append({"article_id": ref.article_id, "title": article.title,
                              "url": ref.url, "draft": draft_id, "plan": plan})
            # Advance state only on success so failures get retried next run.
            source.advance_state(state, article)
            save_state(state_path, state)
        except Exception as e:
            log.exception("Failed processing %s: %s", ref.article_id, e)
            failures.append({"article_id": ref.article_id, "url": ref.url, "error": str(e)})

    log.info("Done. %d processed, %d skipped, %d failed. (%d queued for later)",
             len(processed), len(skipped), len(failures), len(queue))
    return 1 if failures else 0


# ---- drip mode: publish whatever in the queue is due now ----

def run_drip(*, state_path: str, dry_run: bool) -> int:
    """Publish queued drafts whose slot time has arrived. Fired by the drip
    workflow at each slot (Tue/Wed/Fri/Sat/Sun 09/15/21 HKT). Publishes at most
    config.DRIP_MAX_PER_RUN (default 1) per run — even if the run fires late and
    several slots are already due — so it never bursts or hits the rate limit."""
    queue_path = queue_path_for(state_path)
    queue = load_queue(queue_path)
    now_iso = iso_utc(datetime.now(timezone.utc))
    due = sorted((it for it in queue if it.get("publish_at", "") <= now_iso),
                 key=lambda it: it["publish_at"])
    log.info("Drip: %d queued, %d due (now=%s)", len(queue), len(due), now_iso)
    if not due:
        return 0

    if dry_run:
        for it in due:
            log.info("[DRY-RUN] would publish %s — %s", it["draft_id"], it.get("title", ""))
        return 0

    if not config.MATTERS_EMAIL or not config.MATTERS_PASSWORD:
        log.error("MATTERS_EMAIL / MATTERS_PASSWORD not set. Aborting.")
        return 2
    client = MattersClient()
    client.login(config.MATTERS_EMAIL, config.MATTERS_PASSWORD)

    published: set[str] = set()
    failed = 0
    pub_calls = 0
    for it in due[:config.DRIP_MAX_PER_RUN]:
        if pub_calls and pub_calls % 2 == 0:
            log.info("Rate-limit guard: sleeping %ds", config.PUBLISH_WINDOW_SECONDS)
            time.sleep(config.PUBLISH_WINDOW_SECONDS)
        try:
            client.publish_draft(it["draft_id"])   # publish now (no publishAt)
            log.info("Published %s — %s", it["draft_id"], it.get("title", ""))
            published.add(it["draft_id"])
            pub_calls += 1
        except MattersError as e:
            # Keep it queued so the next slot retries.
            log.warning("Publish failed (will retry next slot): %s — %s", e, it.get("title", ""))
            failed += 1

    remaining = [it for it in queue if it["draft_id"] not in published]
    save_queue(queue_path, remaining)
    log.info("Drip done. %d published, %d failed, %d still queued.",
             len(published), failed, len(remaining))
    return 1 if failed else 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Repost articles to Matters.")
    parser.add_argument("--source", required=True, choices=known_sources(),
                        help="Source site to pull from.")
    parser.add_argument("--state", default=None,
                        help="Path to state JSON (default: state/<source>.json).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Don't talk to Matters.")
    parser.add_argument("--publish", action="store_true",
                        help="Publish drafts immediately (default: leave as drafts).")
    parser.add_argument("--bootstrap", action="store_true",
                        help="Record current refs as seen without posting anything.")
    parser.add_argument("--drip", action="store_true",
                        help="Publish due items from the publish queue (no fetch).")
    parser.add_argument("--list-drafts", action="store_true",
                        help="Print the account's drafts (id / state / title) and exit.")
    parser.add_argument("--max", type=int, default=config.MAX_ARTICLES_PER_RUN,
                        help="Cap on articles processed per run.")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    state_path = args.state or f"state/{args.source}.json"
    dry_run = args.dry_run or config.DRY_RUN
    publish = args.publish or config.PUBLISH

    if args.list_drafts:
        client = MattersClient()
        client.login(config.MATTERS_EMAIL, config.MATTERS_PASSWORD)
        for d in client.list_drafts(first=100):
            log.info("DRAFT id=%s state=%s | %s", d.get("id"), d.get("publishState"), d.get("title"))
        return 0

    if args.drip:
        return run_drip(state_path=state_path, dry_run=dry_run)

    return run(
        source_name=args.source,
        state_path=state_path,
        dry_run=dry_run,
        publish=publish,
        max_articles=args.max,
        bootstrap_only=args.bootstrap,
    )


if __name__ == "__main__":
    sys.exit(main())
