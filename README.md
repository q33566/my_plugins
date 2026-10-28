# Google Drive Reader：Claude Code plugin

在 Claude Code 裡詢問 Google Drive 資料夾裡的檔案。這是作者為 OakMega 公司的面試作業
（take-home assignment）寫的 plugin。它把 Claude Code 接到作者架在 Cloudflare Workers
上的遠端 MCP 伺服器（MCP 是 Claude Code 呼叫外部工具的協定）。伺服器用 Google OAuth 讓你
登入，向 Google 要的權限是「讀取整個 Drive」（唯讀），外加你的 email，用來辨識帳號。

它能列出你指定的資料夾及其子資料夾裡的檔案（上限見「使用」一節），並讀取檔案：

- Google 文件、簡報與純文字檔：以文字回傳。Google 試算表只回傳第一個工作表。
- 圖片（PNG、JPEG、GIF、WebP）：以圖片回傳。
- PDF：以檔案回傳，由 Claude 逐頁讀取，掃描版也可以。
- 其他類型：Claude 會被告知無法讀取。

它不能搜尋 Drive，也不能寫入 Drive。

## 安裝

需要 Claude Code 2.1.310 以上（用 `claude --version` 確認），以及一個能開啟你要讀的資料夾
的 Google 帳號。在 Claude Code 裡執行：

```
/plugin marketplace add q33566/my_plugins
/plugin install gdrive-reader@gdrive-reader-marketplace
```

若 Claude Code 要求，再執行 `/reload-plugins` 或重開 Claude Code。

## 登入

1. 執行 `/mcp`，選 gdrive-reader（顯示 **needs authentication**），再選
   **Authenticate**。Claude Code 會開啟瀏覽器。
2. 選你的 Google 帳號。Google 會顯示「Google hasn't verified this app」，因為作者的 OAuth
   應用程式 Kurt-Folder-Reader 沒有送 Google 驗證。點 **Advanced**，再點
   **Go to Kurt-Folder-Reader (unsafe)**。
3. 勾選「See and download all your Google Drive files」，再按 **Continue**。
4. 瀏覽器顯示驗證成功，`/mcp` 裡 gdrive-reader 顯示為已連線。

登入後有兩組 token：

- **Google token**：Google 發給伺服器，伺服器用它讀你的 Drive。
- **Claude Code token**：伺服器發給 Claude Code，Claude Code 用它呼叫伺服器。

Claude Code 會自動更新 Claude Code token，但不會延長期限：登入完成 30 天後必須重新登入。
若你在 Google 撤銷授權，會更早失效。重新登入的步驟同上。

## 使用

- 指定資料夾：「列出 https://drive.google.com/drive/folders/… 裡的檔案」。你的 Google
  帳號能開的資料夾都可以。
- 不指定資料夾：「列出 Drive 資料夾裡的檔案」會列出預設資料夾，也就是 OakMega 為這份作業
  分享的測試資料夾（一個 PDF、一張圖片）。你的帳號需要有它的存取權。
- 接著詢問列出的檔案，例如「讀那個 PDF」「圖片裡有什麼」。

上限：

- PDF 與純文字檔超過 10 MB、圖片超過 5 MB 不讀；回傳的文字超過 20 萬字元時只給前 20 萬字元。
- 資料夾超過 2,000 個項目或 20 層時，只列出一部分。
- 作者的伺服器用 Cloudflare 免費方案，每個請求最多 50 個對外請求、約 10 ms CPU 時間。每個
  子資料夾至少要一個對外請求，讀檔前也會重列整個資料夾，所以子資料夾有幾十個時，列出和讀檔
  都可能失敗；幾 MB 的檔案也可能因 CPU 時間不夠而失敗。
- Google 對未驗證的應用程式限制總共 100 位新使用者，額滿後新帳號無法登入。

## token 與資料存放

- **你的電腦**：Claude Code token 在 macOS 存在鑰匙圈，其他系統存在 Claude Code 的
  credentials 檔。Claude Code 也會把讀過的檔案內容留在 `~/.claude/projects/` 底下。
- **伺服器**：在 Cloudflare Workers KV（Cloudflare 的 key-value 儲存）存一筆你的登入紀錄。
  - 紀錄裡的 Google token 是加密的，解密金鑰又用 Claude Code token 加密；伺服器只存 Claude
    Code token 的 hash。所以只拿到 KV，解不開 Google token。
  - 每次請求都會帶 Claude Code token，伺服器在處理請求時解密，看得到 Google token 和檔案
    內容。所以使用者要信任作者，和使用其他由廠商代管的 connector 一樣。
  - Google token 能讀你整個 Drive。伺服器只讀你指定的資料夾，是程式的行為，不是權限的限制。
  - 紀錄以你的 Google 帳號 ID 為 key、email 為標籤，兩者都是明文。
  - 檔案內容只在處理請求時經過伺服器，不儲存也不記錄。
- **登出**：`/mcp` → gdrive-reader → **Clear authentication**，再到
  <https://myaccount.google.com/permissions> 移除 Kurt-Folder-Reader。移除後紀錄裡的
  Google token 就失效；紀錄本身最長留到 30 天到期。

設計細節見 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 疑難排解

| 狀況 | 處理 |
|---|---|
| 登入失敗，錯誤訊息是「Google Drive read access was not granted」 | 登入時沒勾 Drive 選項。重新登入並勾選。 |
| Google 顯示「Access blocked」或說管理員限制了應用程式 | 公司或學校帳號的管理員封鎖了未驗證的應用程式。改用能開該資料夾的個人 Gmail 帳號。 |
| Claude 用了別的 Google Drive 工具 | 另一個 Google Drive 整合（例如 claude.ai 的 connector）也開著。在 `/mcp` 關掉它，或說「用 gdrive-reader plugin」。 |
| Claude 說檔案在資料夾外（outside the folder） | 伺服器只讀你指定的資料夾和它的子資料夾。請指定包含該檔案的資料夾。資料夾超過上限只列出一部分時，超出的檔案也會這樣回報。 |
| Claude Code 在遠端主機（SSH）上，瀏覽器在你的電腦 | 選 **Authenticate** 時 Claude Code 會印出登入網址，在你電腦的瀏覽器打開並登入。最後瀏覽器跳到的 `localhost` 頁面會打不開，把網址列的完整網址貼給 Claude，它會用來完成登入。 |

## 開發

在 repo 根目錄：

```
(cd backend && npm install && npm test)   # 對模擬的 Google 服務跑登入流程與工具的測試
(cd backend && npx tsc --noEmit)          # 型別檢查
claude plugin validate --strict plugins/gdrive-reader
```
