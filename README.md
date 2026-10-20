# release-pipeline

多 repo 的發版與同步管線範本：每個專案各自建置和發佈 release，一個「feed repo」定期收集所有專案的最新版本，產出一份 JSON feed，讓使用者用一個網址就能拿到全部更新。

適合一個人或小團隊同時維護十幾到上百個 repo 的情境。只用 Python 標準函式庫，GitHub App 簽章另需 `cryptography`。

## 架構

```
本機 release.py                 各專案 release.yml              feed repo sync-feed.yml（每 15 分鐘）
──────────────                 ──────────────────              ─────────────────────────────────────
版本檢查 → leak-guard            建置 → leak-guard → 發佈          抓所有專案的最新 release
→ 推 tag → 觸發 workflow ──────▶  GitHub Release  ◀───────────── → 失敗分級 → 寫入 feed.json → commit
→ 秒級返回
```

## 設計重點

### 1. 發版不等 CI

`release.py` 推完 tag、觸發 release workflow 後就返回。同步工作交給排程 workflow，所以發一輪十幾個版本只要幾秒，不用每個專案等 60～190 秒的建置。

不採用跨 repo 事件（`repository_dispatch`）的原因：那需要在每個專案放一把能寫 feed repo 的權杖。排程只需要一個身分，代價是最多 15 分鐘的延遲。

### 2. 失敗分級

單一專案的暫時錯誤不能擋住其他專案的更新，但任何失敗都不能被靜默吞掉：

| 標記 | 情況 | 處理 |
|---|---|---|
| `[warn] fetch` | 某專案抓取失敗，feed 已有它的舊條目 | 沿用舊條目，下一輪重試 |
| `[FAIL] no-feed-entry` | 抓取失敗，而且沒有舊條目可沿用 | 硬失敗：新專案首發不能被漏掉 |
| `[FAIL] all-failed` | 全部專案都失敗 | 硬失敗：判定為平台問題 |
| `[FAIL] persist` | feed 檔寫入失敗 | 硬失敗 |
| `[FAIL] unexpected` | 分級邏輯遇到未預期狀態 | 硬失敗，不吞 |

`sync_feed.py` 硬失敗時以 exit 2 離開，而且不寫入 feed。workflow 另外做一道反向檢查：exit 0 但輸出有行首 `[FAIL]`，代表分級本身有漏洞，一樣停下。結尾的重點整理以 `[summary]` 開頭，不會被誤判。

每個結果固定只佔一行（多行錯誤訊息會先壓成單行），run summary 第一段就分得出「全綠」和「成功但有警告」。

### 3. 憑證最小化

- 用 **GitHub App** 換取一小時有效的 installation token，取代個人 PAT。run 與 commit 顯示為 bot，不是個人帳號。
- App 私鑰同時接受「檔案路徑」（本機）和「PEM 內容」（CI secret）兩種形狀，以開頭的 `-----BEGIN` 判斷。
- 任何退回個人身分的情況都會印出警告，不會靜默發生。

### 4. 發版前的防洩漏檢查

本機推 tag 前和 CI 發佈前，都可以用 [leak-guard](https://github.com/Lother/leak-guard) 掃描即將送出的內容，有命中或檢查工具本身出錯都不放行（fail-closed）。

### 5. Workflow 的靜默失敗陷阱

`templates/sync-feed.yml` 的註解記錄了幾個「不報錯、但永遠不會再更新」的情況：

- 取消排隊中的 run 等於少同步一次，所以 `cancel-in-progress: false`。
- 被 job 層級 `if:` 跳過的 job，在 `gh run list` 看起來和成功一樣，所以只跳過 step。
- 用管線接 `tee` 之後 `$?` 永遠是 0，要讀 `PIPESTATUS[0]`。
- private repo 改用 self-hosted runner 時，runner group 預設不接 public repo 的工作，job 會永遠排隊。
- 「只給 private repo」的 org secret，在 public repo 裡讀到的是空字串。

## 使用方式

1. **feed repo**：放入 `scripts/`、`config.json`（參考 `config.example.json`），把 `templates/sync-feed.yml` 複製到 `.github/workflows/`。
2. **各專案 repo**：把 `templates/release.yml` 複製到 `.github/workflows/`，換成實際的建置步驟。
3. **憑證**：建立 GitHub App（Contents 讀寫、Actions 寫入），安裝到擁有這些 repo 的帳號或組織，把 App ID 和私鑰存成 `RP_APP_ID`、`RP_APP_KEY` secret。
4. **發版**：

```sh
python scripts/release.py --repo-path ../tool-a --repo your-org/tool-a --tag v1.4.0 --leak-guard
```

`release.py` 會檢查 tag 格式（`vMAJOR.MINOR.PATCH`），以及新版本是否大於所有既有 tag，才推送。被拒絕時，本機建立的 tag 會一併刪掉。

## 測試

```sh
pip install cryptography
python -m unittest discover -s tests -v
```

涵蓋全綠、單一專案暫時失敗沿用舊條目、新專案失敗、全部失敗、寫入失敗、重點整理不會被誤判、版本下限檢查，以及 App 私鑰的兩種形狀。

## 背景

這是從一套實際維運 100 多個 repo 的管線抽出的通用版本，設計脈絡見[多 repo 發版管線的四個設計決策](https://lother.github.io/notes/release-pipeline.html)。

以 AI coding agent 協作開發：架構、規則與審查由作者負責。

## 授權

MIT
