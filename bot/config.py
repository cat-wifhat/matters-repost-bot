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

MAX_ARTICLES_PER_RUN = int(os.environ.get("MAX_ARTICLES_PER_RUN", "10"))

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
