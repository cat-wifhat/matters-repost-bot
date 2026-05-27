# p-articles → Matters 自動轉載 Bot

每逢周一及周四，自動把 [虛詞 p-articles](https://p-articles.com/) 主站新出現的文章，
以草稿形式建立到你的 [Matters](https://matters.town/@mattershklit) 帳號，等你按發佈。

- **執行環境**：GitHub Actions（免費，依 cron 自動跑，不靠你電腦開機）
- **發佈模式**：預設儲存為**草稿**，你可在 Matters app 內檢查排版/圖片後手動按發佈
- **轉載內容**：標題、內文、圖片、tags、原文連結與 credit、虛詞無形 social media 連結，licence 設為「作者保留所有權利」(`arr`)
- **新文章判定**：記錄每個分類最大 article ID，每次只處理 ID 比上次大的

---

## 1. 一次性設定（建議流程）

### Step 1 — 建立 GitHub Repo

1. 在 GitHub 建立一個 **private** repo（例如 `matters-repost-bot`）。
2. 把這個資料夾推上去：
   ```bash
   cd /Users/willisho/Downloads/repost-bot
   git init
   git add .
   git commit -m "initial commit"
   git branch -M main
   git remote add origin git@github.com:你的GitHub帳號/matters-repost-bot.git
   git push -u origin main
   ```

### Step 2 — 設定 Matters 登入 Secrets

到 repo 的 **Settings → Secrets and variables → Actions → New repository secret**，加兩個：

| Name | Value |
|---|---|
| `MATTERS_EMAIL` | 你的 Matters 登入 email |
| `MATTERS_PASSWORD` | 你的 Matters 密碼 |

> 你的密碼只存在 GitHub Secrets 內，加密儲存，bot 在每次跑時即時取用。
> 我（Claude）並沒有保存你的密碼。

### Step 3 — Bootstrap（首次記錄起點）

第一次跑要先告訴 bot「以後從這個點開始算新文章」，否則它會把整個首頁 44 篇全部
當作新文章嘗試轉載。

到 repo **Actions** tab → 選 **Repost p-articles to Matters** → 點 **Run workflow** →
把 `bootstrap` 設為 `true` → Run。

跑完之後 repo 會自動 commit 一個 `state.json`，內容類似：
```json
{
  "last_seen_ids": {
    "critics": 5993,
    "heteroglossia": 5992,
    "issues": 5934,
    "nianhua": 1191,
    "one_take": 5976,
    "works": 5987
  }
}
```

### Step 4 — 測試一次 Dry Run

再次 Run workflow，這次 `dry_run=true`、`bootstrap=false`，看看 log 輸出。
此時應該顯示 `New articles to repost: 0`，因為剛 bootstrap 過。

如果想實際試一篇，可手動把 `state.json` 內某個分類的 ID 調小（例如 `critics` 從
`5993` 改為 `5991`），commit 後再跑一次（這次 `dry_run` 留 `false`），bot 會把
critics/5992 與 critics/5993 轉成 draft。到 Matters app 看草稿夾就有。

---

## 2. 之後它如何自己跑？

GitHub Actions cron 已設定為：

```
0 2 * * 1,4
```

即 UTC 02:00 = **香港時間每周一、周四上午 10:00**。
要改時間，編輯 [.github/workflows/repost.yml](.github/workflows/repost.yml) 內的 cron。

每次跑：
1. 抓 p-articles 首頁，找所有 `/<分類>/<id>.html` 連結
2. 比對 `state.json`，找出 ID 比上次大的文章
3. 對每篇新文章：
   - 在 Matters 建空草稿 → 上傳圖片（用 Matters 的 `directImageUpload`，URL 直接喂給它，
     由 Matters 自己 fetch 圖檔）→ 把 title/body/tags/credit/social links 塞入草稿，license=`arr`
4. 處理成功就更新 `state.json` 並 commit 回 repo

預設**不會直接發佈**——你要去 Matters 草稿夾按發佈。
如果想自動發佈，把 workflow 的 `publish` input 設 `true`，或在 `.github/workflows/repost.yml`
裡把 cron job 的 env 加 `PUBLISH: "true"`。

---

## 3. 本機測試

```bash
cd /Users/willisho/Downloads/repost-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # 編輯 .env 填你的 Matters email/password

# Dry run（不會碰 Matters）：
DRY_RUN=true python -m bot.main --state state.json

# 真的試發一篇（會留作 draft）：
set -a; source .env; set +a
python -m bot.main --state state.json
```

可選 flag：
- `--bootstrap` 記錄當前 max ID，不發任何文章
- `--dry-run` 印出會做什麼，不碰 Matters
- `--publish` 直接發佈（預設只存草稿）
- `--max 5` 單次最多處理 N 篇（預設 10）

---

## 4. 故障排查

| 現象 | 怎辦 |
|---|---|
| Bot 沒抓到新文章 | 看 Actions log 的「Found N article links on homepage」。若 < 20 篇可能 p-articles 首頁改版，要改 [bot/scraper.py](bot/scraper.py) 內的 `ARTICLE_URL_RE` |
| Matters 登入失敗 (`Login failed`) | 確認 Secrets 對；如帳號是 wallet 登入，要先到 Matters 設定 email 密碼 |
| 圖片缺失 | `directImageUpload` 可能 fail（p-articles 圖片 server 偶有 503）。Bot 會 log 警告並繼續，draft 內仍有圖片位置但 src 不變——你 Matters 內手動補圖即可 |
| 排版有問題 | 草稿模式下你可在 Matters editor 內修，不會直接公開 |
| 文章漏轉 | 不會自動補——`state.json` 用「>= last_seen+1」邏輯。要補，手動把該分類 ID 調小，下次跑會抓 |
| 短時間內出超多新文（如首頁 update 大爆發） | 預設 cap 10 篇/次，避免 Matters rate limit。剩下的下次跑會繼續處理 |

---

## 5. 檔案結構

```
.
├── .github/workflows/repost.yml   # GitHub Actions cron job
├── bot/
│   ├── config.py                  # env vars, social links 配置
│   ├── scraper.py                 # p-articles 抓取 + HTML 清洗
│   ├── matters_client.py          # Matters GraphQL client
│   └── main.py                    # orchestrator
├── requirements.txt
├── state.json                     # 由 bot 自動維護，記錄已處理的 article ID
└── README.md
```

---

## 6. 重要備註

- **授權**：你聲明已獲虛詞授權轉載。Repo 內請保留此 README 段落為證。
- **隱私**：Matters 密碼只儲存於 GitHub Secrets（AES 加密、有 access log）。
- **state.json 會被 commit 回 repo**，這是 GitHub Actions 跨 run 持續狀態的標準做法。如果你不想公開 state，請保持 repo private。
- **Matters API 變動**：若 Matters 將來改了 GraphQL schema，可能需要更新 [bot/matters_client.py](bot/matters_client.py)。可用 [server.matters.news/graphql](https://server.matters.news/graphql) 的 introspection 查最新 schema。
