# music-bot

一個跑在自己電腦上的 Discord 機器人：指定頻道裡有人傳音樂專輯封面或歌詞照片，它就用 `claude -p` 辨識專輯、歌手、曲風，追加進一個 Excel，存下照片，再回覆上傳者（附上找歌詞與聽歌的搜尋連結；歌詞轉錄功能已停用）。程式開著才會運作。

- 設計與原因：[`docs/architecture.md`](docs/architecture.md)
- 需求：[`docs/requirements.md`](docs/requirements.md)

## 第一次設定

1. 建立 Discord 伺服器與頻道，取得 `CHANNEL_ID`：[`docs/discord-server-setup.md`](docs/discord-server-setup.md)
2. 建立機器人、打開 **Message Content Intent**、取得 token：[`docs/discord-bot-setup.md`](docs/discord-bot-setup.md)
3. 複製 `.env.example` 存成 `.env`，填入四個設定（`DISCORD_TOKEN`、`CHANNEL_ID`、`CLAUDE_MODEL`、`DATA_DIR`）。
4. 確認電腦上的 `claude` 已安裝並登入（終端機輸入 `claude --version` 有反應），而且系統環境變數**沒有**設定 `ANTHROPIC_API_KEY`（有的話程式啟動時會警告）。

## 啟動

在專案資料夾（有 `main.py` 的那一層）執行：

```
uv run python main.py
```

第一次執行 uv 會自動建立環境並安裝套件。看到 `已上線：…，只聽頻道 …` 就是成功了。按 `Ctrl + C` 關閉。

- 第一次啟動**不會**補處理頻道裡的舊圖片，從現在開始處理。
- 之後每次啟動，會補處理「關機期間漏掉的圖片」（補到上次正常處理的最後一張為止）。補處理不受下面的排隊上限限制，積壓的圖會全部排進去慢慢處理。

## 資料放哪裡

都在 `DATA_DIR` 底下（預設 `./data`，已被 `.gitignore` 擋掉，不會被 commit）：

| 路徑 | 內容 |
| --- | --- |
| `music_records.xlsx` | 紀錄，一直往下加。**機器人在跑的時候不要直接開它**（Windows 會鎖住檔案），要看就先複製一份 |
| `photos/` | 照片，檔名 `YYYY-MM-DD hh-mm-ss 簡稱.副檔名` |
| `state.json` | 第一次啟動的時間（開機補處理用） |
| `pending_records.jsonl` | 只有 Excel 被鎖住寫不進去時才會出現的暫存，下次可寫入時自動補上 |
| `pending_records.rejected.jsonl` | 只有暫存檔裡出現讀不懂的行時才會出現，把那些行另存起來給人看 |
| `logs/agent.log` | 執行紀錄（每張圖的處理步驟與分析結果；不記 token、上傳者暱稱與訊息文字） |

## 測試

```
uv run pytest
```

測試都用假的 `claude` 和假的 Discord 物件，不會花額度。

## 四段安全防護

程式裡有四段防護，**預設全部開啟**。每一段都用固定格式的註解圈起來，各有一個開關常數（放在檔案最上面，故意不放進 `.env`）。

| 防護 | 擋什麼 | 檔案 | 開關 | 大約行號 |
| --- | --- | --- | --- | --- |
| 防護 1：工作目錄限制 | 讓 `claude -p` 讀到 `DATA_DIR` 裡別人的照片和紀錄 | `src/music_bot/analyzer.py` | `ENABLE_GUARD_1` | 92–127 |
| 防護 2：Prompt Injection 隔離 | 上傳者的字被當成給 `claude -p` 的指令 | `src/music_bot/analyzer.py`（加 `prompts/guard2_data_only.md`） | `ENABLE_GUARD_2` | 133–153 |
| 防護 3：檔名安全性清洗 | 專輯名稱含 `..`、`/`、`\`、`:` 讓檔案跑到別的地方 | `src/music_bot/storage.py` | `ENABLE_GUARD_3` | 39–80 |
| 防護 4：Excel 公式注入防護 | 以 `=` `+` `-` `@` 開頭的文字在 Excel 裡被當成公式 | `src/music_bot/storage.py` | `ENABLE_GUARD_4` | 166–180 |

行號會隨編輯而移動，要找確切位置請執行：

```
grep -n "==== 防護" src/music_bot/*.py
```

要關掉某一段來對照：把該檔案最上面對應的 `ENABLE_GUARD_N` 改成 `False`（測試裡也有「關掉後攻擊會成功」的對照案例）。

## 注意

- **每張圖都會用掉你自己的 Claude 額度**，頻道裡的人可以一直傳。請只在信任的人的頻道使用。機器人內建上限：同時排隊 30 張、每人 5 張、單張逾時 120 秒、每張最多呼叫 `claude` 2 次。
- **`.env` 與 `DATA_DIR` 的內容絕對不能 commit。** token 外洩時，馬上到 Developer Portal 按 Reset Token。
