# CLAUDE.md

Guidance for working in this repo. `README.md` is the user-facing overview and
`docs/operations-log.md` is the change/incident log — **keep all three in sync**.
Repo owner is **cat-wifhat** (renamed from dw-wq); use `gh auth switch --user
cat-wifhat` for git/gh.

## What this is

A multi-source repost bot that mirrors articles from independent Hong Kong
media sites to corresponding **Matters.town** accounts. Each run pulls a
source's recent articles, figures out which are new, and **auto-publishes** them
(virtue on a weekly drip schedule; witness immediately). Runs on GitHub Actions
cron — no always-on server.

Sources currently wired up:

| Source name        | Site               | Matters account / state file              | Filter                    |
|--------------------|--------------------|-------------------------------------------|---------------------------|
| `p_articles`       | 虛詞・無形 (p-articles.com) | `@mattershklit` / `state/mattershklit.json` | per-category; skips third-party reposts |
| `thewitnesshk`     | 法庭線 (thewitnesshk.com)   | `@mattershkrec` / `state/mattershkrec_witness.json` | 專題 / feature (category id 8) |
| `thecollectivehk`  | 集誌社 (thecollectivehk.com)| `@mattershkrec` / `state/mattershkrec_collective.json` | 深度 (id 5) — **workflow disabled** (SiteGround blocks GHA IPs) |

## Architecture

The orchestrator is **source-agnostic**; everything site-specific lives behind
the `Source` abstraction.

- `bot/main.py` — orchestrator. `run()`: `list_recent_article_refs` → filter via
  `is_new` → sort oldest-first (`publish_order_key`) → cap at
  `MAX_ARTICLES_PER_RUN` → for each: `fetch_article`, skip if
  `repost_skip_reason`, `create_filled_draft` (draft + images + content), then
  apply the disposition from `publish_schedule` (publish now / enqueue / leave
  draft), `advance_state` + save **on success only**. `run_drip()`: publishes
  due items from the queue (drip mode). Also owns content composition and the
  dry-run/bootstrap paths.
- `bot/sources/base.py` — `Source` ABC + `Article`/`ArticleRef` dataclasses;
  scheduling hooks (`publish_order_key`, `publish_schedule`, `PUBLISH_NOW`,
  `iso_utc`) and `repost_skip_reason`; shared HTTP helpers
  (`make_scraper_session`, `make_curl_cffi_session`, `fetch_image_bytes`,
  `fetch_json`).
- `bot/sources/__init__.py` — the source **registry**. Add new sources here
  (`get_source` / `known_sources` drive the `--source` CLI choices).
- `bot/sources/{p_articles,thewitnesshk,thecollectivehk}.py` — concrete sources.
- `bot/matters_client.py` — minimal Matters GraphQL client: `emailLogin`,
  `putDraft`, `singleFileUpload`, `publishArticle` (with optional `publishAt`),
  `deleteDraft`, `list_drafts` (used for drip dedup).
- `bot/config.py` — env vars + generic constants only. **Per-source config
  (credit links, social URLs, header format) lives in the source module, not here.**
- `.github/workflows/repost-*.yml` — one workflow per source/account.
- `state/*.json` — per-source dedup state, committed back to the repo each run.

## Key decisions (and the reasons behind them)

- **Auto-publish via our own drip queue — NOT Matters `publishAt`.** Matters
  rate-limits `publishArticle` to ~2 calls / 12 min, and that cap *also* blocks
  its native scheduling (future `publishAt`), even in bulk. So we don't use
  Matters scheduling. Instead: the creation run publishes the 2 oldest
  immediately and drops the rest into `state/<account>_queue.json`
  (`[{draft_id, publish_at, title}]`); a **drip workflow** fires on a schedule
  and `run_drip` publishes **≤1 due article per run** (`DRIP_MAX_PER_RUN`),
  after a dedup check against live draft state (skip/drop anything no longer
  `unpublished` → never republish → no duplicates). One-per-run keeps posts
  spread out and each run fast (no timeout, no rate-limit hit). 法庭線 has no
  queue — it just publishes ≤2 immediately (few 專題 articles).
- **Skip third-party reposts (虛詞).** 虛詞 sometimes republishes pieces from
  other platforms, marked「授權轉載自」in the body; `repost_skip_reason` skips
  those (only 虛詞). We mirror first-party content only.
- **Duplicate protection (two layers).** Workflow commit steps use
  `if: always()` so a partial failure can't skip persisting state (state only
  advances per successful article). And the creation run does a **title dedup**:
  it loads existing titles via `list_drafts` and skips any article already on the
  account — the last line of defence if state still can't be pushed. (Drip has
  its own draft-state dedup.)
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
- **Crons (UTC).** Creation: `0 22 * * 0,3` (虛詞 = Mon/Thu 06:00 HKT),
  `0 22 * * 1,4` (法庭線 = Tue/Fri 06:00 HKT). Drip: `0 1,7,13 * * 0,2,3,5,6`
  (虛詞 = Tue/Wed/Fri/Sat/Sun 09:00/15:00/21:00 HKT). GHA cron is unreliable
  (delayed/dropped runs), which is why drip is ≤1-per-run + due-based + dedup.
- **Publishing is oldest-first** so the Matters timeline reads chronologically.
- **License is always `arr`** (author retains all rights); **tags capped at 3**
  (Matters limit).

## State shapes (differ by source — don't assume one schema)

- `p_articles`: `{"last_seen_ids": {"<category>": <max numeric id>, ...}}` —
  per-category cursor; `is_new` is `numeric_id > last_seen[category]`.
- WordPress sources: `{"last_seen_id": <wp post id>}` — single integer cursor.
- **Publish queue** (虛詞 only): `state/mattershklit_queue.json` —
  `[{draft_id, publish_at (ISO UTC), title}]`, drained by the drip workflow.
  Committed back each run like state.

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

# Dry run (no Matters calls; shows the publish plan):
python -m bot.main --source p_articles --dry-run --publish
# Real run — state path defaults to state/<source>.json:
python -m bot.main --source p_articles --publish
# Drip: publish due queued items (used by the drip workflow):
python -m bot.main --source p_articles --drip
```

Flags: `--source` (required), `--state PATH`, `--dry-run`, `--publish`,
`--bootstrap`, `--drip`, `--list-drafts`, `--max N`. Env equivalents: `DRY_RUN`,
`PUBLISH`, `MAX_ARTICLES_PER_RUN`, `DRIP_MAX_PER_RUN`, `PUBLISH_WINDOW_SECONDS`.
Exit codes: `0` success, `1` some articles failed, `2` missing auth/config.

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
