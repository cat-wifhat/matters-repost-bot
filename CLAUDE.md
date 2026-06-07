# CLAUDE.md

Guidance for working in this repo. (The `README.md` is an older single-source
write-up and is partly stale — trust this file and the code over it.)

## What this is

A multi-source repost bot that mirrors articles from independent Hong Kong
media sites to corresponding **Matters.town** accounts. Each run pulls a
source's recent articles, figures out which are new, and creates them as
**drafts** on Matters (left for manual review/publish by default). Runs on
GitHub Actions cron — no always-on server.

Sources currently wired up:

| Source name        | Site               | Matters account / state file              | Filter                    |
|--------------------|--------------------|-------------------------------------------|---------------------------|
| `p_articles`       | 虛詞・無形 (p-articles.com) | `@mattershklit` / `state/mattershklit.json` | per-category, by numeric id |
| `thewitnesshk`     | 法庭線 (thewitnesshk.com)   | `state/mattershkrec_witness.json`         | 焦點 category (id 28)       |
| `thecollectivehk`  | 集誌社 (thecollectivehk.com)| `state/mattershkrec_collective.json`      | 深度 / in-depth category (id 5) |

## Architecture

The orchestrator is **source-agnostic**; everything site-specific lives behind
the `Source` abstraction.

- `bot/main.py` — orchestrator. Loop: `list_recent_article_refs` → filter via
  `is_new` → cap at `MAX_ARTICLES_PER_RUN` → for each: `fetch_article`,
  `repost_article` (create draft, upload images, fill content), then
  `advance_state` + save **on success only**. Also owns content composition
  (`header + featured images + body + credit`) and the dry-run/bootstrap paths.
- `bot/sources/base.py` — `Source` ABC + the `Article`/`ArticleRef`
  dataclasses, plus shared HTTP helpers: `make_scraper_session` (cloudscraper),
  `make_curl_cffi_session` (TLS-impersonation for stricter WAFs),
  `fetch_image_bytes`, and `fetch_json` (retrying GET for flaky WP/Cloudflare
  endpoints).
- `bot/sources/__init__.py` — the source **registry**. Add new sources here
  (`get_source` / `known_sources` drive the `--source` CLI choices).
- `bot/sources/{p_articles,thewitnesshk,thecollectivehk}.py` — concrete sources.
- `bot/matters_client.py` — minimal Matters GraphQL client: `emailLogin`,
  `putDraft` (create/update), `singleFileUpload` (multipart), `publishArticle`.
- `bot/config.py` — env vars + generic constants only. **Per-source config
  (credit links, social URLs, header format) lives in the source module, not here.**
- `.github/workflows/repost-*.yml` — one workflow per source/account.
- `state/*.json` — per-source dedup state, committed back to the repo each run.

## Key decisions (and the reasons behind them)

- **Drafts, not auto-publish.** Default leaves drafts for human review of
  layout/images. `--publish` opts in; when publishing it sleeps
  `PUBLISH_INTERVAL_MINUTES` (30) between articles because Matters caps at
  ~2 publishes / 12 min. A publish failure is swallowed (draft already filled)
  rather than failing the article.
- **Images are uploaded as bytes, not by URL.** We download each image with the
  *source's* session and push it to Matters via `singleFileUpload`. Matters'
  server-side image fetcher (`directImageUpload`) gets Cloudflare-blocked on
  these sites and leaves 404 assets. The first image is also uploaded a second
  time as the `cover` asset.
- **State advances only after a successful repost** (and never in `--dry-run`),
  so failures get retried next run and dry runs don't silently bump cursors.
  (See commit `f956062`.)
- **State is committed back to the repo** — standard GHA pattern for
  cross-run persistence. Workflows retry the push with `pull --rebase` to
  survive parallel-workflow races.
- **Per-source HTTP transport.** p_articles uses cloudscraper; the two WordPress
  sites override `_make_session` to use `curl_cffi` with a Safari TLS
  fingerprint because their WAF blocks plain requests / chrome fingerprints
  from datacenter IPs.
- **Cron at off-peak UTC** (`0 22 * * 0,3` = Mon & Thu 06:00 HKT) to dodge the
  oversubscribed top-of-hour GHA scheduler.
- **License is always `arr`** (author retains all rights); **tags capped at 3**
  (Matters limit).

## State shapes (differ by source — don't assume one schema)

- `p_articles`: `{"last_seen_ids": {"<category>": <max numeric id>, ...}}` —
  per-category cursor; `is_new` is `numeric_id > last_seen[category]`.
- WordPress sources: `{"last_seen_id": <wp post id>}` — single integer cursor.

First run (empty state) or `--bootstrap` records currently-visible refs as seen
and posts nothing, so old articles aren't backfilled.

## Conventions

- **Adding a source:** subclass `Source`, implement the abstract methods, keep
  all site-specific HTML/links inside the module, register it in
  `bot/sources/__init__.py`, add a `state/<account>.json` (bootstrap it), and add
  a `.github/workflows/repost-<account>.yml` (copy an existing one; set `SOURCE`
  and `STATE_FILE` env).
- `ArticleRef.article_id` is **opaque to the orchestrator** — sources define the
  format (e.g. `"critics/5993"` vs a WP id). Carry listing-time metadata in
  `ArticleRef.extra` to avoid re-fetching in `is_new`/`advance_state`.
- The orchestrator never imports a concrete source — go through `get_source`.
- Credentials are always read from `MATTERS_EMAIL` / `MATTERS_PASSWORD`;
  workflows map per-account secrets onto those two names.

## Running it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill MATTERS_EMAIL / MATTERS_PASSWORD

# Dry run (no Matters calls):
python -m bot.main --source p_articles --dry-run
# Real run — state path defaults to state/<source>.json:
python -m bot.main --source p_articles
```

Flags: `--source` (required), `--state PATH`, `--dry-run`, `--publish`,
`--bootstrap`, `--max N`. Env equivalents: `DRY_RUN`, `PUBLISH`,
`MAX_ARTICLES_PER_RUN`, `PUBLISH_INTERVAL_MINUTES`. Exit codes: `0` success,
`1` some articles failed, `2` missing auth/config.

## Gotchas

- Featured-image `<figure>` blocks **must** contain both a self-closing
  `<img/>` with `data-asset-id` and an empty `<figcaption>`, or Matters' editor
  parser crashes (`Cannot read properties of undefined ('firstChild')`). See
  `_build_featured_html` in `bot/main.py`.
- Multipart uploads need the `apollo-require-preflight` /
  `x-apollo-operation-name` headers or Apollo's CSRF guard rejects them.
- Tech stack: Python 3.11+, `requests`, `cloudscraper`, `curl_cffi`,
  `beautifulsoup4` + `lxml`. Matters GraphQL endpoint:
  `https://server.matters.news/graphql`.
