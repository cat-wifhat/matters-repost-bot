"""One-off check: does Matters' scheduled publishing (publishAt) work?

Creates a throwaway draft on the logged-in account, schedules it ~30 min ahead,
reads back publishState/publishAt to confirm it's 'pending' (scheduled, NOT yet
public), then deletes it. Nothing should remain public.

Run via the _verify-publishat workflow (it has the Matters secrets); the local
.env has no credentials. Safe to delete this file after verification.
"""
from __future__ import annotations

import datetime
import logging
import sys

from bot import config
from bot.matters_client import MattersClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("verify-publishat")


def main() -> int:
    if not config.MATTERS_EMAIL or not config.MATTERS_PASSWORD:
        log.error("MATTERS_EMAIL / MATTERS_PASSWORD not set")
        return 2

    c = MattersClient()
    c.login(config.MATTERS_EMAIL, config.MATTERS_PASSWORD)

    draft_id = c.create_empty_draft(title="【測試・可刪】publishAt 驗證")
    c.update_draft(
        draft_id,
        title="【測試・可刪】publishAt 驗證",
        content="<p>scheduled-publish verification — will be deleted.</p>",
        license="arr",
    )
    log.info("created draft %s", draft_id)

    now = datetime.datetime.now(datetime.timezone.utc)
    pub_at = (now + datetime.timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    log.info("now(UTC)=%s  scheduling publishAt=%s", now.strftime("%Y-%m-%dT%H:%M:%SZ"), pub_at)

    res = c.publish_draft(draft_id, publish_at=pub_at)
    log.info("publishArticle returned: %s", res)

    state = res.get("publishState")
    ok = state == "pending"
    log.info("==== RESULT: publishState=%s (expect 'pending'), publishAt=%s -> %s ====",
             state, res.get("publishAt"), "PASS" if ok else "NEEDS REVIEW")

    # Clean up so nothing publishes in 30 min.
    try:
        out = c.delete_draft(draft_id)
        log.info("deleteDraft(%s) -> %s  (cleaned up; nothing left public/pending)", draft_id, out)
    except Exception as e:  # noqa: BLE001
        log.warning("CLEANUP FAILED for draft %s (%s) — DELETE IT MANUALLY before %s",
                    draft_id, e, pub_at)

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
