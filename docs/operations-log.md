# Operations Log

A human-readable record of notable changes, decisions, incidents, and the
commands used — to complement `git log`. Newest first. Dates are 東八區 (HKT)
unless marked UTC.

> Quick status (2026-07-02): auto-publish is live for 虛詞 (@mattershklit) and
> 法庭線 (@mattershkrec). 集誌社 is disabled. Publishing is driven by our own
> **drip queue**, not Matters' scheduling. GitHub owner is **cat-wifhat**.

---

## 2026-07-02 — Duplicate posts from a skipped state-commit

**Symptom:** two 虛詞 articles (Jamie Vardy, Granta) were published twice
(06-29 and again 07-02).

**Cause:** the 06-29 creation run published the 2 immediate articles fine, but a
later article's fetch hit a network error → the bot returned exit 1 → the
workflow's `Commit updated state` step (no `if:`) was **skipped**. So the state
cursor never advanced (and the queue writes weren't committed). The next
creation run (07-02) re-saw those articles as new and re-published them. The
creation path has no dedup (unlike the drip path), so lost state = duplicates.

**Fix:** (1) the commit steps now run with `if: ${{ always() }}` in all three
workflows, so successfully-published progress (state advances only per success)
is persisted even when a later article fails. (2) Last-line dedup: the creation
run loads existing titles via `list_drafts` and **skips any article whose title
is already on the account**, so even if state can't be persisted the same piece
isn't published twice. User manually deleted the dup copies.

---

## 2026-06 — Full auto-publish + drip scheduling (虛詞 & 法庭線)

### Goal
Switch from draft-only to **fully automatic publishing**, respecting Matters'
rate limit (**2 publishes / 12 min**), and spread 虛詞's posts across the week
so the feed isn't flooded (few articles → bursts look like spam / 洗版).

### Final design (what's live now)
- **法庭線 (@mattershkrec)** — publishes **immediately, ≤2 per run** (it has few
  「專題」articles). No queue.
- **虛詞 (@mattershklit)** — weekly drip:
  - Creation run (Mon/Thu HKT): publish the **2 oldest immediately**, drop the
    rest into a **publish queue** `state/mattershklit_queue.json`
    (`[{draft_id, publish_at, title}]`), oldest-first; overflow beyond the
    week's slots stays a plain draft.
  - **Drip workflow** `repost-mattershklit-drip.yml` fires on a schedule and
    publishes **at most 1 due article per run** (`DRIP_MAX_PER_RUN`, `run_drip`),
    with a **dedup** check (skips/drops any queued draft that is no longer
    `unpublished`, so a run that publishes-but-doesn't-commit can't duplicate).
- **We do NOT use Matters' native scheduling (`publishAt`)** — see incident
  below. Timing is driven entirely by when the drip workflow fires.

### Why not Matters `publishAt`
`publishArticle` is rate-limited to ~2 calls / 12 min, and **that cap also
applies to scheduling calls** (future `publishAt`) — even bulk-scheduling from
the Matters editor is blocked. So scheduling 5+ articles in one run fails with
`ACTION_LIMIT_EXCEEDED`. We bypass it entirely with the self-driven drip queue.

### Incidents (and fixes)
1. **Test article published to @mattershklit.** A one-off `publishAt`
   verification created + scheduled + `deleteDraft`-ed a test draft; but a
   scheduled publish, once queued by Matters, is **not** cancelled by deleting
   the draft — so it went public ~30 min later. Lesson: `deleteDraft` does not
   cancel an already-scheduled publish; don't run live-account tests without
   asking. (User deleted it.)
2. **First auto-publish runs dropped 5/7 to drafts.** `publishAt` scheduling hit
   the rate limit (see above). First fix throttled the calls; ultimately
   replaced by the drip queue.
3. **Drip burst + duplicates (洗版).** The old `run_drip` published **all** due
   items per run (throttled). GitHub's scheduler dropped/coalesced the slot
   crons, so a day's slots piled up and published together; the throttle then
   hit the job timeout → **cancelled before committing the queue** → the next
   run **re-published** the same drafts. `末世狂沙` and `從編輯…三刊編輯對談`
   each went public **3×**. Fixes: publish **1 per run**, spread across the day,
   **dedup by live draft state**, and reset the queue to the unpublished items.
   **Manual cleanup still needed:** delete the duplicate published copies of
   those two titles (keep 1 each).

### Verified working (2026-06-26/27)
The 4 queued 虛詞 articles drip-published one per run (06-26 10:21 / 15:22,
06-27 04:51 / 09:27 UTC), spread out, no burst, no duplicates; queue drained to
`[]`.

---

## 2026-06 — 法庭線 section change: 焦點 → 專題
`thewitnesshk` mirrored 焦點 (category 28), which is mostly breaking news and
flooded the draft box. Switched to **專題 / feature (category 8** = the site's
`/feature/` archive). State cursor carried over, so no old features backfilled.

---

## 2026-06 — 虛詞: skip third-party reposts
虛詞 sometimes republishes pieces authorized from other platforms, marked
「（文章授權轉載自「<平台>」）」 in the body (e.g. Openbook閱讀誌). We only mirror
虛詞's own content, so `PArticlesSource.repost_skip_reason()` skips any article
whose body contains a `SKIP_BODY_MARKERS` substring (default `授權轉載自`). Only
applies to 虛詞. See commit `feat(p_articles): skip 虛詞's third-party reposts`.

---

## 2026-06 — 集誌社 (thecollectivehk): blocked, then disabled
The site runs SiteGround's "Security Optimizer", which serves an `sgcaptcha`
interstitial to **datacenter IPs (GitHub Actions)** on every path — `wp-json`
and RSS feed alike. Confirmed: identical code succeeds from a residential IP,
fails from GHA. Public proxies were unreliable. Per the user, 集誌社 was
**abandoned for now**: `gh workflow disable repost-mattershkrec-collective.yml`.
The RSS-feed rescue attempt remains on branch `fix/thecollectivehk-rss-feed`.

---

## 2026-06-14 — GitHub account rename + contributor cleanup
- Account renamed **dw-wq → cat-wifhat** (display `catwifhat`, id `279000002`).
  Repos are now `cat-wifhat/matters-repost-bot` and
  `cat-wifhat/matters-newsletter-bot`; local remotes updated.
- **bluelake (`bluelake60s-cmd`, the user's personal account)** appeared in the
  contributors because one commit was authored with `bluelake60s@gmail.com`.
  Rewrote history (author/committer email) across both repos and force-pushed;
  commits now attribute to `cat-wifhat`
  (`279000002+cat-wifhat@users.noreply.github.com`). The repo-page "Contributors"
  sidebar is a cached view and lags a rewrite; the underlying data / contributors
  API were verified clean. GitHub Support was contacted to purge the cache
  (they cannot manually clear it; it recomputes on its own).
- Use the `cat-wifhat` gh account for git/gh (`gh auth switch --user cat-wifhat`);
  never `bluelake60s-cmd`.

---

## Operational reference (commands)

```bash
# Which gh account is active (must be cat-wifhat for pushes)
gh api user --jq .login
gh auth switch --user cat-wifhat

# Recent workflow runs
gh run list --workflow repost-mattershklit.yml --limit 10
gh run list --workflow repost-mattershklit-drip.yml --limit 10
gh run view <run-id> --log            # full log
gh run view <run-id> --log-failed     # only failed steps

# Inspect the publish queue on GitHub
git show origin/main:state/mattershklit_queue.json

# List the account's drafts (id / state / title) — needs Matters secrets, run via Actions
python -m bot.main --source p_articles --list-drafts

# Dry-run the drip (no publishing)
gh workflow run repost-mattershklit-drip.yml -f dry_run=true

# Manual creation run (dry-run, shows the publish plan)
python -m bot.main --source p_articles --state state/mattershklit.json --dry-run --publish

# Re-enable 集誌社 later
gh workflow enable repost-mattershkrec-collective.yml
```

### Key files
- `bot/main.py` — orchestrator; `run()` (create + enqueue), `run_drip()` (drip).
- `bot/sources/p_articles.py` — 虛詞 source + weekly-grid `publish_schedule`.
- `bot/sources/base.py` — `publish_schedule` default (2 immediate) + `iso_utc`.
- `bot/config.py` — `DRIP_MAX_PER_RUN`, `PUBLISH_WINDOW_SECONDS`, cap.
- `state/mattershklit_queue.json` — 虛詞 publish queue (committed each run).
- `.github/workflows/repost-mattershklit-drip.yml` — drip schedule.
