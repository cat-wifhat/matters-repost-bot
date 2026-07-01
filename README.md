# Matters 多來源自動轉載 Bot

把香港獨立媒體的新文章，自動轉載到對應的 [Matters](https://matters.town) 帳號。
在 GitHub Actions 上依 cron 自動執行，不靠本機開機。

> 詳細架構見 [`CLAUDE.md`](CLAUDE.md)；歷次改動、決策與事故記錄見
> [`docs/operations-log.md`](docs/operations-log.md)。本檔為總覽。

---

## 來源與帳號

| 來源 | 網站 | Matters 帳號 / state | 欄目 | 狀態 |
|---|---|---|---|---|
| `p_articles` | 虛詞・無形 | `@mattershklit` / `state/mattershklit.json` | 全部欄目（跳過二手轉載） | ✅ 運作中 |
| `thewitnesshk` | 法庭線 | `@mattershkrec` / `state/mattershkrec_witness.json` | 專題 (feature, cat 8) | ✅ 運作中 |
| `thecollectivehk` | 集誌社 | `@mattershkrec` / `state/mattershkrec_collective.json` | 深度 (cat 5) | ⛔ 已停用* |

\* 集誌社網站的 SiteGround 防護會攔截資料中心（GitHub Actions）IP，暫時放棄；
workflow 已 disable。修復試驗保留在分支 `fix/thecollectivehk-rss-feed`。

GitHub repo 擁有者為 **cat-wifhat**（`cat-wifhat/matters-repost-bot`）。

---

## 發布行為（全自動發布）

不使用 Matters 內建的「預約發布」(`publishAt`)——它與即時發布共用「12 分鐘 2 篇」
限速，一次多篇會被擋。改由我們自己的**待發佇列 + 分時段觸發**控制時間。

### 虛詞（@mattershklit）— 週期性分散發布
- **創建執行**（東八區週一、週四早上）：把新文章建成草稿，**最舊 2 篇即時發布**，
  其餘依序放入待發佇列 `state/mattershklit_queue.json`；超出當週時段的留草稿。
- **drip 執行**（東八區週二、三、五、六、日 **09:00 / 15:00 / 21:00**）：
  每次只發**到期的最舊 1 篇**（`DRIP_MAX_PER_RUN`），並先查草稿狀態**去重**
  （已發過的直接剔除、不重發）。每日最多 3 篇、分早午晚，不會洗版。

### 法庭線（@mattershkrec）— 即時發布
- 一進草稿即時發布，一次最多 2 篇（專題文章不多，基本上即發）。

---

## 排程（GitHub Actions cron，時間為 UTC）

| Workflow | cron | 東八區 | 作用 |
|---|---|---|---|
| `repost-mattershklit.yml` | `0 22 * * 0,3` | 週一、四 06:00 | 虛詞：抓新文、即時發 2 篇、其餘入佇列 |
| `repost-mattershklit-drip.yml` | `0 1,7,13 * * 0,2,3,5,6` | 週二三/五六日 09/15/21 | 虛詞：每次發佇列中到期的 1 篇 |
| `repost-mattershkrec-witness.yml` | `0 22 * * 1,4` | 週二、五 06:00 | 法庭線：抓新文、即時發 |

> GitHub 排程常延遲數分鐘至十幾分鐘，甚至偶爾整次跳過；drip「每次 1 篇 + 到期才發」
> 的設計對此有容錯（漏掉的下次補上、永不爆發）。

---

## 一次性設定

1. 在 repo **Settings → Secrets and variables → Actions** 設定登入憑證：
   - 虛詞：`MATTERS_EMAIL` / `MATTERS_PASSWORD`
   - 法庭線／集誌社：`MATTERSHKREC_EMAIL` / `MATTERSHKREC_PASSWORD`
2. 首次或加新來源時，用 `--bootstrap` 記錄目前文章為「已見」，避免倒灌舊文。

---

## 本機執行

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # 填 MATTERS_EMAIL / MATTERS_PASSWORD

# Dry run（不碰 Matters，印出會做什麼／發布計劃）：
python -m bot.main --source p_articles --dry-run --publish

# 實際執行（state 預設 state/<source>.json）：
python -m bot.main --source p_articles --publish
```

主要 flag：`--source`（必填）、`--state PATH`、`--dry-run`、`--publish`、
`--bootstrap`、`--drip`（只發佇列到期項）、`--list-drafts`、`--max N`。
環境變數：`DRY_RUN`、`PUBLISH`、`MAX_ARTICLES_PER_RUN`、`DRIP_MAX_PER_RUN`。

---

## 內容規則

- **只轉第一手原創**：虛詞內文含「授權轉載自」（二手轉自其他平台）者**自動跳過**。
- 標題、內文、圖片、tags、原文連結與 credit、社群連結一併轉載。
- 圖片以 bytes 上傳到 Matters（不餵 URL，避免被來源站 Cloudflare 擋）。
- licence 固定 `arr`（作者保留所有權利）；tags 上限 3（Matters 限制）。
- 發布順序**舊文先發**（時間順），Matters 時間線與原站一致。

---

## 常用維運指令

```bash
gh auth switch --user cat-wifhat                       # 推送需用此帳號
gh run list --workflow repost-mattershklit-drip.yml    # 看 drip 執行
gh run view <run-id> --log                             # 看日誌
git show origin/main:state/mattershklit_queue.json     # 看待發佇列
gh workflow run repost-mattershklit-drip.yml -f dry_run=true   # 試跑 drip
gh workflow enable repost-mattershkrec-collective.yml  # 日後重啟集誌社
```

更完整的流程、事故與指令：見 [`docs/operations-log.md`](docs/operations-log.md)。

---

## 技術棧

Python 3.11+、`requests` / `cloudscraper` / `curl_cffi`、`beautifulsoup4` + `lxml`。
Matters GraphQL：`https://server.matters.news/graphql`。
