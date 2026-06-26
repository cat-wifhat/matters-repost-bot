"""Generic config — env vars and shared constants.

Per-source things (credit links, social URLs, header format) live inside each
bot/sources/<name>.py module, not here.
"""
import os

MATTERS_API = "https://server.matters.news/graphql"

# Credentials are mapped per workflow via repository Secrets, but the bot always
# reads them from these two env var names (workflows do the renaming).
MATTERS_EMAIL = os.environ.get("MATTERS_EMAIL", "")
MATTERS_PASSWORD = os.environ.get("MATTERS_PASSWORD", "")

DRY_RUN = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")
PUBLISH = os.environ.get("PUBLISH", "").lower() in ("1", "true", "yes")

# Cap per run. Raised to 20 for auto-publish: a 虛詞 Thursday cycle can schedule
# up to 11 (2 immediate + 9 slots) plus a few overflow drafts.
MAX_ARTICLES_PER_RUN = int(os.environ.get("MAX_ARTICLES_PER_RUN", "20"))

# Matters caps publishArticle at 2 calls per 12 minutes — and that cap applies
# to *scheduled* calls (future publishAt) too, not just immediate publishes. So
# auto-publish throttles to 2 calls per window and sleeps this long between
# windows (12 min + buffer).
PUBLISH_WINDOW_SECONDS = int(os.environ.get("PUBLISH_WINDOW_SECONDS", "780"))

# Drip mode publishes at most this many articles per run (default 1 = true drip).
# GitHub's scheduler often delays a run by hours; by then a whole day's slots are
# all "due", and publishing them together floods the feed. A hard per-run cap
# keeps it to one post per scheduled slot no matter how late the run fires.
DRIP_MAX_PER_RUN = int(os.environ.get("DRIP_MAX_PER_RUN", "1"))

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
