# Discord 機器人（Bot）設定指南（Windows）

這份文件帶你一步一步做：

1. 在 Discord Developer Portal 建立 Application 和 Bot
2. 打開 **Message Content Intent**（一定要）
3. 取得 **Bot Token**
4. 選擇 Bot 權限，產生邀請網址，把 Bot 邀進你的伺服器
5. 把 token 和頻道編號放進 `.env`

**請先完成** [`docs/discord-server-setup.md`](discord-server-setup.md)。你需要已經有自己的伺服器，以及記好的 `CHANNEL_ID`。

> **名詞先講一下**
> - **Discord Developer Portal（開發者後台）**：Discord 給開發者管理機器人的網站，**只有英文介面**。
> - **Application（應用程式）**：你在後台建立的「專案」，Bot 是它底下的一個功能。
> - **Bot Token**：機器人的**密碼**。拿到它的人可以完全控制你的機器人。

---

## ⚠️ Bot Token 的鐵則（先讀這段）

> **Bot Token 絕對不能：**
> - 貼給**任何人**（包含同學、老師、朋友、Discord 上的陌生人）
> - 貼進**任何 AI 對話**（包含 ChatGPT、Gemini，**也包含這個 Claude Code 對話**）
> - **commit 進 Git**，或上傳到 GitHub
> - 放進截圖、螢幕錄影、直播畫面
> - 寫在程式碼裡
>
> **只能放在** 專案根目錄的 `.env` 檔案，而這個檔案已經被 `.gitignore` 擋掉，不會被 commit。
>
> **萬一外流了：** 馬上回到 Developer Portal 按 **Reset Token**（見第三部分），舊 token 立刻失效，再把新的放進 `.env`。

---

## 第一部分：建立 Application 與 Bot

1. 打開瀏覽器（Edge 或 Chrome 都可以），到：

   ```
   https://discord.com/developers/applications
   ```

2. 如果要求登入，用你**平常使用 Discord 的帳號**登入。
3. 畫面**右上角**點藍紫色的 **「New Application」** 按鈕。
4. 在 **「Name」** 欄位輸入：`Music-Recognition-Bot`
5. 勾選下方的同意條款方塊（I agree to the Discord Developer Terms of Service...）。
6. 點 **「Create」**。
7. 進到這個 Application 的設定頁。**左邊**有一排選單（General Information、Installation、OAuth2、Bot……）。
8. 點左邊的 **「Bot」**。
   - 新版的 Application 建立後就已經有 Bot，不需要另外按 Add Bot。
9. （選用）在 Bot 頁面，可以改機器人的**頭像**和**名稱**，這是它在 Discord 裡顯示的樣子。

### 1-1 把 Bot 設成「只有我能邀請」

1. 還在 **Bot** 頁面，往下找 **「Public Bot」** 這一項。
2. 把它的開關**關掉**（變灰色）。
   - 關掉之後，只有你（擁有者）可以把這個 Bot 邀進伺服器，別人拿到連結也加不了。
3. 下面的 **「Requires OAuth2 Code Grant」** 保持**關閉**。
4. 如果頁面最下方跳出「Save Changes」，點它。

---

## 第二部分：打開 Message Content Intent（一定要）

### 2-1 為什麼一定要打開

這是整個設定裡**最容易漏掉**、也**最重要**的一步。

- Discord 為了保護隱私，**預設不讓機器人看見訊息的內容**，包含：
  - 使用者**打的字**（上傳圖片時附帶的說明文字）
  - 使用者**附上的檔案和圖片**（附件的網址就藏在這裡）
- 我們的 Bot 需要：
  1. **讀到圖片附件**：才能下載專輯封面或歌詞照片去分析。
  2. **讀到附帶的文字**：上傳者有時會寫「這是周杰倫的新專輯」，這句話會當成辨識的參考提示（當成資料，不是指令，見 `architecture.md` 的防護 2）。
- **沒有打開的話會怎樣**：
  - 程式一啟動就會報錯，訊息類似 `PrivilegedIntentsRequired`（缺少特權權限）。
  - 或者 Bot 雖然連上，卻**像看不到圖一樣完全沒有反應**，因為它收到的附件是空的。
- 這個開關叫「特權」（Privileged），因為 Discord 認為讀訊息內容比較敏感，所以要你手動同意。你的伺服器很小，不需要 Discord 另外審核，直接打開即可。

### 2-2 打開步驟

1. 在左邊選單點 **「Bot」**。
2. 往下捲到 **「Privileged Gateway Intents」** 這一區。裡面有三個開關：
   - Presence Intent
   - Server Members Intent
   - **Message Content Intent**
3. 只把 **「Message Content Intent」** 的開關**打開**（變成藍色／綠色）。
   - 另外兩個**保持關閉**。我們不需要，開了反而多給權限。
4. 畫面下方會跳出一條儲存列，點 **「Save Changes」**。
   - 沒有存檔的話，開關會跳回去。

**確認**：重新整理頁面（`F5`），Message Content Intent 仍然是打開的。

---

## 第三部分：取得 Bot Token

> 再提醒一次：下面的步驟會讓畫面上出現 token。**不要截圖、不要錄影、不要讓別人看到你的螢幕。**

1. 還在左邊的 **「Bot」** 頁面。
2. 找到 **「Token」** 這一區，點 **「Reset Token」**。
   - 新建的 Application 有時會直接顯示 token 和 Copy 按鈕。看到就直接按 Copy 即可；沒有的話就用 Reset Token。
3. 跳出確認視窗，點 **「Yes, do it!」**。
4. 如果要求你輸入 Discord 密碼或雙重驗證碼，照做。
5. token 會**只顯示這一次**，是一長串字母與數字。點旁邊的 **「Copy」**。
6. 立刻打開記事本，貼上並記成這樣（先放著，等等要放進 `.env`）：

   ```
   DISCORD_TOKEN=貼上你剛複製的那一長串
   ```

7. **如果你沒複製到、或關掉頁面了**：沒關係，回來再按一次 Reset Token 就會得到新的。
   - 注意：每次 Reset，舊的 token 就立刻失效。

---

## 第四部分：選擇權限，產生邀請網址，把 Bot 邀進伺服器

### 4-1 要勾哪些權限

遵守「**最小權限**」原則：只給 Bot 真的用得到的權限。

| 權限（英文） | 要勾嗎 | 用途 |
| --- | --- | --- |
| **View Channels**（檢視頻道） | ✅ 要勾 | 看得到頻道才能收到圖片。**你原本清單沒列，但一定要** |
| **Send Messages**（傳送訊息） | ✅ 要勾 | 回覆辨識結果 |
| **Read Message History**（讀取訊息歷史） | ✅ 要勾 | 開機後補處理漏掉的圖片；用「回覆」功能回在原訊息底下 |
| **Add Reactions**（新增反應） | ✅ 要勾 | 貼 ⏳ ✅ 🚫 ❌ |
| **Attach Files**（附加檔案） | ⬜ **這一版先不勾** | 這一版的機器人只回文字，不會上傳檔案。以後想讓它傳檔案再開 |
| Administrator（管理員） | ❌ **絕對不要勾** | 權限太大，Bot 被攻破就等於整個伺服器被接管 |
| Manage Messages、Embed Links 等其他項目 | ❌ 不勾 | 用不到。拿掉自己貼的表情符號，不需要 Manage Messages |

### 4-2 產生邀請網址

1. 在左邊選單點 **「OAuth2」**。
2. 往下找到 **「OAuth2 URL Generator」** 這一區（有些版本它是 OAuth2 底下的另一個子選單，點進去即可）。
3. 在 **「Scopes」** 裡，只勾 **`bot`**。
   - **不要**勾 `applications.commands`（我們沒有用斜線指令）。
4. 勾了 `bot` 之後，下方會多出 **「Bot Permissions」** 這一區。依照 4-1 的表格，勾這四項：
   - [x] View Channels
   - [x] Send Messages
   - [x] Read Message History
   - [x] Add Reactions
5. 如果有「Integration Type」，選 **「Guild Install」**。
6. 捲到最下面，看到 **「Generated URL」**，是一長串 `https://discord.com/oauth2/authorize?client_id=...` 的網址。點旁邊的 **「Copy」**。

> 這條網址**不是 token**，不是密碼，可以放心用。但因為你關掉了 Public Bot，只有你自己能用它邀請 Bot。

### 4-3 把 Bot 邀進你的伺服器

1. 打開瀏覽器新分頁，貼上剛剛複製的網址，按 `Enter`。
2. 畫面會要你**選擇伺服器**。在下拉選單裡選你剛建立的伺服器（例如 `音樂辨識實驗室`）。
   - 選單裡看不到你的伺服器？表示你登入的 Discord 帳號和建伺服器的帳號不同，或伺服器不是你擁有的。
3. 點 **「繼續」**（Continue）。
4. 確認要給的權限和你勾的四項一致，點 **「授權」**（Authorize）。
5. 可能會出現「我不是機器人」的驗證，照著點完。
6. 看到「已授權」（Authorized）就成功了。

**確認**：回到 Discord，在你的伺服器右邊成員清單，會看到 **Music-Recognition-Bot**，旁邊標著 **BOT**。它現在是**離線（灰色）**，這是正常的，程式跑起來它才會上線。

### 4-4 （選用）限制 Bot 只能用在指定頻道

> 先說清楚：**真正決定機器人只處理哪個頻道的，是程式裡的 `CHANNEL_ID`**，不是 Discord 的權限設定。下面是額外加的保險，不做也可以正常運作。

新建的伺服器，預設所有成員（包含 Bot）都看得到所有頻道，所以 Bot 其實也看得到其他頻道。如果你想讓它在 Discord 這邊也只能碰指定頻道：

1. 在伺服器左側點伺服器名稱 → **「伺服器設定」**（Server Settings）→ **「身分組」**（Roles）→ 找到 **「Music-Recognition-Bot」** 這個身分組。
2. 在它的權限分頁，把「檢視頻道」等四項**關掉**。
3. 回到頻道清單，對 `#音樂封面與歌詞辨識` 按右鍵 → **「編輯頻道」**（Edit Channel）→ **「權限」**（Permissions）。
4. 點 **「＋」** 新增身分組，選 **Music-Recognition-Bot**。
5. 把這四項設為 **✅（允許）**：檢視頻道、傳送訊息、讀取訊息歷史、新增反應。
6. 只靠這樣，Bot 還是會看到 `@everyone` 允許的其他公開頻道。要完全看不到，需要把其他頻道也設成私人。你的伺服器現在只有預設頻道，這一步**不是必要的**。

---

## 第五部分：把 token 和頻道編號放進 `.env`

這一步要在 Windows 上建立一個叫 `.env` 的檔案。**最常見的坑是檔名變成 `.env.txt`**，請照著做。

### 5-1 建立檔案

1. 按 `Win` 鍵，輸入 **記事本**，打開 **記事本**（Notepad）。
2. 貼上下面四行，並把前兩行換成你自己的值：

   ```
   DISCORD_TOKEN=貼上你的 token
   CHANNEL_ID=貼上你的頻道編號
   CLAUDE_MODEL=sonnet
   DATA_DIR=C:\music-bot-data
   ```

   - 等號 `=` 前後**不要有空格**，值**不要加引號**。
   - `CLAUDE_MODEL`、`DATA_DIR` 先照上面寫，之後可以改。
3. 按 `Ctrl + Shift + S`（另存新檔）。
4. 左邊選到專案資料夾：`C:\Users\user\music-bot`。
5. **「存檔類型」**（Save as type）下拉選單，選 **「所有檔案 (*.*)」**。
6. **「檔案名稱」**輸入（**包含雙引號**）：

   ```
   ".env"
   ```

7. 點 **「存檔」**。

### 5-2 檢查檔名沒有被加上 .txt

1. 開啟 **檔案總管**，進到 `C:\Users\user\music-bot`。
2. 點上方的 **「檢視」** → **「顯示」** → 勾選 **「副檔名」**。
3. 確認檔名是 `.env`，**不是** `.env.txt`。
   - 如果是 `.env.txt`：對檔案按右鍵 →「重新命名」→ 把 `.txt` 刪掉。

### 5-3 確認它不會被 commit

1. 在專案資料夾空白處按住 `Shift` 再按右鍵，選 **「在此處開啟 PowerShell 視窗」**（或「在終端機中開啟」）。
2. 輸入：

   ```
   git check-ignore -v .env
   ```

3. 應該會看到類似這樣的一行（代表 `.env` 被 `.gitignore` 擋住了）：

   ```
   .gitignore:153:.env    .env
   ```

4. 再輸入 `git status`，確認清單裡**沒有出現 `.env`**。如果出現了，**不要 commit**，先停下來處理。

---

## 完成檢查

- [ ] Developer Portal 裡有 `Music-Recognition-Bot` 這個 Application
- [ ] **Public Bot** 已關閉
- [ ] **Message Content Intent** 已打開且存檔（重新整理後仍是打開的）
- [ ] 我已取得 token，並且**沒有貼給任何人、沒有貼進任何 AI 對話**
- [ ] 邀請網址只勾了 `bot`，權限只有 View Channels、Send Messages、Read Message History、Add Reactions
- [ ] 伺服器成員清單看得到 `Music-Recognition-Bot`（BOT 標籤，離線是正常的）
- [ ] 專案資料夾裡有 `.env`，檔名不是 `.env.txt`
- [ ] `git check-ignore -v .env` 顯示 `.env` 被擋，`git status` 看不到它

---

## 常見問題

| 狀況 | 可能原因與解法 |
| --- | --- |
| 程式啟動時出現 `PrivilegedIntentsRequired` | Message Content Intent 沒打開，或打開了沒按 Save Changes。回第二部分 |
| Bot 在線，但傳圖都沒反應 | 同上；或 `CHANNEL_ID` 填錯；或 Bot 在那個頻道沒有「檢視頻道」權限 |
| 出現 `LoginFailure`／`Improper token` | token 複製不完整、前後多了空白或引號、或已經被 Reset 過。重新 Reset 並複製一次 |
| Bot 能收到圖，但回覆失敗 | 缺 Send Messages 或 Read Message History 權限。回 4-1 檢查，或在頻道的權限設定補上 |
| Bot 貼不上 ⏳ | 缺 Add Reactions 權限 |
| 授權頁面的伺服器下拉選單是空的 | 登入的帳號不是伺服器擁有者，或需要「管理伺服器」權限 |
| token 不小心貼出去了 | 馬上到 Bot 頁面按 **Reset Token**，並把新的放進 `.env`。舊的立刻作廢 |
