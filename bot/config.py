import os

SOURCE_BASE = "https://p-articles.com"

MATTERS_API = "https://server.matters.news/graphql"

MATTERS_EMAIL = os.environ.get("MATTERS_EMAIL", "")
MATTERS_PASSWORD = os.environ.get("MATTERS_PASSWORD", "")

DRY_RUN = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")
PUBLISH = os.environ.get("PUBLISH", "").lower() in ("1", "true", "yes")

MAX_ARTICLES_PER_RUN = int(os.environ.get("MAX_ARTICLES_PER_RUN", "10"))

STATE_PATH = os.environ.get("STATE_PATH", "state.json")

# Credit links shown at the bottom of every repost. Order, labels, and URLs
# all mirror the manual format used by @mattershklit on matters.town.
CREDIT_LINKS = [
    ("虛詞・無形網站",     "https://p-articles.com/"),
    ("虛詞・無形Facebook", "https://www.facebook.com/formless.particles"),
    ("虛詞・無形YouTube",  "https://www.youtube.com/@formless.particles"),
    ("虛詞・無形Patreon",  "https://www.patreon.com/thehouseofhk_literature"),
]

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
