# CLAUDE.md

這是一個基於 Discord Bot 的音樂專輯封面與歌詞識別 Agent，能自動辨識上傳圖片中的音樂資訊、歌詞與統計紀錄。

## 開始任何工作前

先讀 `docs/architecture.md`。架構、`claude -p` 的 JSON 約定、Excel 欄位、檔名規則、驗證清單都以它為準；需求背景在 `docs/requirements.md`。兩份文件有出入時，以 `architecture.md` 為準，並告訴使用者哪裡不同。

## 四段防護

程式裡有四段安全防護，**一開始全部開啟**。每一段都要用下面這組註解**逐字**圈起來（之後教學會依這些標記逐段開關，所以字串不能改、不能省）：

```python
# ==== 防護 1：工作目錄限制 開始 ====
# ==== 防護 1：工作目錄限制 結束 ====

# ==== 防護 2：Prompt Injection 隔離 開始 ====
# ==== 防護 2：Prompt Injection 隔離 結束 ====

# ==== 防護 3：檔名安全性清洗 開始 ====
# ==== 防護 3：檔名安全性清洗 結束 ====

# ==== 防護 4：Excel 公式注入防護 開始 ====
# ==== 防護 4：Excel 公式注入防護 結束 ====
```

- 每一段各有一個開關常數（例如 `ENABLE_GUARD_1 = True`），放在該檔案最上面，**不放進 `.env`**。
- 預設一律是開啟（`True`）。沒有使用者明確要求，不要關閉、移除或繞過任何一段。
- 標記之間只放該防護的程式碼，不要把無關的邏輯塞進去，也不要讓兩段防護的標記互相重疊。
- 各防護預計所在的檔案與要擋的威脅，見 `docs/architecture.md` 第 12 節。

## 不可以 commit 的東西

- **`.env`**：裡面有 `DISCORD_TOKEN`，絕對不能 commit、不能貼進對話、不能印進 log。`.env` 已列在 `.gitignore`。
- **`DATA_DIR` 裡的所有內容**：照片（`photos/`）、Excel（`music_records.xlsx`）、`state.json`、`pending_records.jsonl`、`logs/`，一律不 commit。`DATA_DIR` 預期放在 repo 外面；若使用者把它設在 repo 內，要先確認它已列進 `.gitignore`，再做任何 commit。
- 只能 commit `.env.example`（欄位名稱與假值）。
- 每次 commit 前先用 `git status` 確認沒有上述檔案被加進去。
