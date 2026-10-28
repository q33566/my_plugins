# Google Drive Reader：Claude Code plugin

在 Claude Code 裡詢問 Google Drive 資料夾裡的檔案。這個 plugin 把 Claude Code 接到作者架在
Cloudflare Workers 上的遠端 MCP 伺服器（MCP 是讓 Claude Code 這類 AI 應用程式呼叫外部
工具的開放協定）。你用 Google 帳號登入後，伺服器取得讀取你整個 Drive 的唯讀權限，以及你的帳號
ID 與 email（用來辨識帳號）。

它能列出指定資料夾、其子資料夾，以及其中捷徑指向的檔案與資料夾（連同那些資料夾的子資料夾，
可能在指定資料夾之外；上限見「使用」一節），並讀取這些檔案：

- Google 文件、簡報與純文字檔：以文字回傳。Google 試算表只回傳第一個工作表。
- 圖片（PNG、JPEG、GIF、WebP）：以圖片回傳。
- PDF：以檔案回傳，由 Claude 逐頁讀取，掃描版也可以。
- 其他類型：伺服器回覆這種類型無法讀取。

它不能搜尋 Drive，也不能寫入 Drive。

## 安裝

需要 Claude Code 2.1.310 以上（用 `claude --version` 確認）。你的 Google 帳號也必須能開啟
要讀的資料夾。在 Claude Code 裡執行：

```
/plugin marketplace add https://github.com/q33566/my_plugins
/plugin install gdrive-reader@gdrive-reader-marketplace
```

若 Claude Code 要求，再執行 `/reload-plugins` 或重開 Claude Code。

## 登入

1. 執行 `/mcp`，選 gdrive-reader（顯示 **needs authentication**），再選
   **Authenticate**。Claude Code 會開啟瀏覽器。
2. 選你的 Google 帳號。Google 會顯示「Google hasn't verified this app」。Kurt-Folder-Reader
   是作者的伺服器在 Google 登記的名稱，因為沒有送 Google 審核，才出現這個警告。點
   **Advanced**，再點 **Go to Kurt-Folder-Reader (unsafe)**。
3. 勾選「See and download all your Google Drive files」，再按 **Continue**。
4. 瀏覽器頁面告訴你可以關閉，`/mcp` 裡 gdrive-reader 不再顯示 needs authentication。

登入完成 30 天後必須重新登入，步驟同上。

## 登入後的兩組 token

登入其實是兩段 OAuth（讓你授權程式存取資料、又不必交出密碼的標準協定），會產生兩組 token
（有權限範圍、會過期的存取憑證）：

- **Google token**：Google 發給伺服器，伺服器用它讀你的 Drive；只存在伺服器，Claude Code
  拿不到。
- **Claude Code token**：伺服器發給 Claude Code，Claude Code 每次呼叫伺服器都帶著它。

每組都有一張 **access token**（用來存取，約一小時過期）和一張 **refresh token**（只用來換新的
access token）。Claude Code 會自動換新，你不必每小時重新登入；但換新不會延長 30 天的登入期限。

登入的每一步、流程圖與保護措施，見 [docs/oauth-flow.md](docs/oauth-flow.md)。

## 使用

- 指定資料夾：「列出 https://drive.google.com/drive/folders/… 裡的檔案」。你的 Google
  帳號能開的資料夾都可以。
- 不指定資料夾時，伺服器讀作者設定的範例資料夾（一個 PDF、一張圖片），只有能開這個資料夾
  的帳號讀得到；一般使用請指定你自己的資料夾。
- 接著詢問列出的檔案，例如「讀那個 PDF」「圖片裡有什麼」。

上限：

- PDF 與純文字檔超過 10 MB、圖片超過 5 MB 不讀；回傳的文字超過 20 萬字元時只給前 20 萬字元。
- 資料夾（含所有子資料夾）超過 2,000 個項目或 20 層時，只列出一部分。
- 作者的伺服器用 Cloudflare 免費方案：Claude Code 每呼叫一次伺服器，伺服器最多再發出 50 個
  請求（含呼叫 Google 和伺服器存放授權資料的 Workers KV，見下一節），計算時間最多 10 ms（等網路回應的時間不算）。
  每個子資料夾（所有層合計）至少要一個請求，而且讀檔前會重列整個資料夾（確認檔案在指定的資料
  夾裡），因此子資料夾接近 50 個時，列出和讀檔都可能整個失敗，Claude 會回報錯誤，而不是只
  列出一部分。這通常比 2,000 個項目的上限先碰到。幾 MB 的檔案也可能因計算時間不夠而讀取失敗。
- Google 規定未審核的應用程式（這裡是 Kurt-Folder-Reader）總共最多 100 位新使用者，額滿後沒登入過的帳號無法登入。

## token 與資料存放

- **你的電腦**：Claude Code 存兩張 Claude Code token：約一小時過期的 access token，和登入
  完成後 30 天內有效的 refresh token。macOS 存在鑰匙圈，其他系統存在 Claude Code 的憑證檔。refresh token
  被偷的話，偷的人能透過伺服器讀你的 Drive，最長到登入滿 30 天，所以要和密碼一樣保護。Claude Code 也會把
  讀過的檔案內容留在 `~/.claude/projects/` 底下。
- **伺服器**：在 Cloudflare Workers KV（Cloudflare 的鍵值儲存）存一筆你的授權資料。
  - 授權資料裡有 Google 的 access token 和 refresh token，都是加密的；解密金鑰只能用 Claude
    Code token 解開（登入途中，Claude Code 換到 token 之前的幾分鐘內，改用伺服器發給 Claude Code 的授權碼保護），
    而伺服器只存 Claude Code token 與授權碼的雜湊值。所以即使有人拿到 KV 裡的全部
    資料，也解不開 Google token。
  - 伺服器處理每次呼叫時，用 Claude Code token 解密，因此看得到 Google token 和檔案內容。
    所以你必須信任作者，就像使用其他由廠商代管的雲端服務一樣。
  - Google token 能讀你整個 Drive。伺服器只讀 Claude 在工具呼叫中指定的資料夾、它的子資料夾，
    以及其中捷徑指向的檔案與資料夾（連同那些資料夾的子資料夾），捷徑可以指向資料夾外；沒指定
    資料夾時讀範例資料夾。這只是程式的做法，不是安全邊界：檔案裡的文字也可能讓 Claude 改指定別的
    資料夾。
  - 授權資料以你的 Google 帳號 ID 當鍵、email 當標籤，兩者都是明文。Claude Code token 和登入時
    經瀏覽器網址傳給 Claude Code 的授權碼，開頭也都帶著你的 Google 帳號 ID。
  - 檔案內容只在處理呼叫時經過伺服器，不儲存也不記錄。
- **登出**：`/mcp` → gdrive-reader → **Clear authentication**，再到
  <https://myaccount.google.com/permissions> 移除 Kurt-Folder-Reader。移除後，授權資料裡的
  Google token 就失效；授權資料本身最晚在登入完成滿 30 天時到期。

設計細節見 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 疑難排解

| 狀況 | 處理 |
|---|---|
| 登入失敗，錯誤訊息是「Google Drive read access was not granted」 | 登入時沒勾「See and download all your Google Drive files」。重新登入並勾選。 |
| Google 顯示「Access blocked」或說管理員限制了應用程式 | 公司或學校帳號的管理員封鎖了未審核的應用程式。改用能開該資料夾的個人 Gmail 帳號。 |
| Claude 回報找不到資料夾或無法存取 | 你的 Google 帳號開不了這個資料夾。確認網址正確，並用能開它的帳號登入。 |
| Claude 用了別的 Google Drive 工具 | 另一個 Google Drive 整合（例如 claude.ai 提供的）也開著。在 `/mcp` 關掉它，或說「用 gdrive-reader plugin」。 |
| Claude 說檔案在資料夾外（outside the folder） | 伺服器只讀指定的資料夾、它的子資料夾和其中捷徑指向的檔案與資料夾（連同那些資料夾的子資料夾）。請指定包含該檔案的資料夾。資料夾超過上限只列出一部分時，超出的檔案也會這樣回報。 |
| Claude Code 在遠端主機（SSH）上，瀏覽器在你的電腦 | 選 **Authenticate** 時 Claude Code 會印出登入網址，在你電腦的瀏覽器打開並登入。最後瀏覽器轉到的 `localhost` 頁面會打不開，把網址列的完整網址貼到 Claude Code 的對話框，Claude 會用它完成登入。 |

## 開發

在 repo 根目錄：

```
(cd backend && npm install && npm test)   # 對模擬的 Google 服務跑登入流程與工具的測試
(cd backend && npx tsc --noEmit)          # 型別檢查
claude plugin validate --strict plugins/gdrive-reader
```
