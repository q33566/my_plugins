# Google Drive Reader：Claude Code plugin

在 Claude Code 裡詢問 Google Drive 資料夾裡的檔案。這個 plugin 把 Claude Code 接到作者架在
Cloudflare Workers 上的遠端 MCP 伺服器（MCP 是讓 Claude Code 這類 AI 應用程式呼叫外部工具的開放協定）。你用
Google 帳號登入後，伺服器取得讀取你整個 Drive 的唯讀權限，以及你的帳號 ID 與 email（用來辨識
帳號）。登入怎麼運作，見「登入時發生什麼」一節。

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
/plugin marketplace add q33566/my_plugins
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

## 登入時發生什麼（OAuth 流程）

Claude Code 要透過伺服器讀你的 Drive，但不該拿到你的 Google 密碼。OAuth 是解決這件事的標準
協定：你同意授權後，發 token 的一方（**授權方**）就發 **token** 給你同意的那支程式。
token 是一串別人猜不到的字串，有權限範圍、會過期、可撤銷；程式之後每次存取資料都帶著它。

這裡有兩支程式要拿 token：伺服器和 Claude Code。所以登入其實是兩段 OAuth：

- **伺服器向 Google 拿 token**（下稱 Google token）。授權方是 Google，你在 Google 的同意畫面
  同意。伺服器用這組 token 讀你的 Drive；它只存在伺服器，Claude Code 拿不到。
- **Claude Code 向伺服器拿 token**（下稱 Claude Code token）。授權方是伺服器，你按
  **Authenticate** 就是同意。

每一段都會拿到兩種 token：**access token** 用來存取，約一小時過期；**refresh token** 只
用來換新的 access token，效期長得多。Claude Code 每次用 refresh token 換
access token 時，伺服器也會發一張新的 refresh token 取代舊的；Google 那段則是 Google 沒給新的
refresh token，就沿用舊的。Claude Code 每次呼叫伺服器時帶的是 access token。

### 為什麼要經過瀏覽器

這裡授權的結果靠瀏覽器轉址送到拿 token 的程式：

- 伺服器和 Google 那一段，你是在瀏覽器裡的 Google 頁面同意的，Google 把瀏覽器轉回伺服器的網址，
  把結果交給伺服器。
- Claude Code 和伺服器那一段，Claude Code 只在你電腦的 localhost 上等結果，伺服器從網路上連
  不到；只有同樣在你電腦上的瀏覽器連得到，所以伺服器把瀏覽器轉到那個 localhost 網址。

但靠轉址送的東西都寫在網址上，會留在瀏覽紀錄和收到請求的伺服器日誌裡。所以
每一段 OAuth 都分兩個動作：

1. 瀏覽器只轉交一個**授權碼**：只能用一次、幾分鐘就過期，本身讀不了資料。
2. 收到授權碼的程式，再自己直接連到授權方，用授權碼換 token。token 不出現在任何網址上。

### 實際步驟

登入前，Claude Code 會先向伺服器**註冊**：登記自己在你電腦上的接收網址（本機位址，例如
`http://localhost:<port>/callback`，port 每次可以不同），取得一個**用戶端 ID**（伺服器用來認出這個 Claude Code 的編號）。任何程式
都能這樣註冊，不需要作者核准。之後的登入網址會帶著用戶端 ID，伺服器在第 ⑤ 步就照這個用戶端
登記的接收網址轉送。

下表會用到兩個詞，用途在下一節「保護措施」說明：**隨機值**是每次登入當場產生、別人猜不到的
字串；**雜湊值**是對隨機值做單向運算的結果，從雜湊值推不回原值。

按下 **Authenticate** 之後，依序發生下面六步。「經瀏覽器」是靠轉址把東西送過去，「直接連線」
是程式自己連過去，不經過瀏覽器。**client secret** 是伺服器在 Google 登記時拿到、只有伺服器和
Google 知道的密碼。第 ②③④ 步是伺服器和 Google 那一段，第 ①⑤⑥ 步是 Claude Code
和伺服器那一段。

| 步驟 | 方式 | 發生什麼 |
|---|---|---|
| ① | 經瀏覽器 | Claude Code 開始在註冊時登記的接收網址等候（只有本機連得到），產生隨機值 A，然後開瀏覽器到伺服器的登入網址，網址上帶著用戶端 ID 和 A 的雜湊值。 |
| ② | 經瀏覽器 | 伺服器產生隨機值 B，以及這次登入專用的另一個隨機值 state，在瀏覽器留一個存著 state 雜湊值的 cookie。接著把瀏覽器轉到 Google 的同意畫面，網址上帶著 B 的雜湊值和 state。 |
| ③ | 經瀏覽器 | 你按 **Continue**，Google 把瀏覽器轉回伺服器，網址上帶著授權碼和原本的 state。 |
| ④ | 直接連線 | 伺服器把授權碼、B 的原值和伺服器的 client secret 交給 Google，換到 Google token，加密存放。 |
| ⑤ | 經瀏覽器 | 伺服器發一個自己的授權碼（綁著發起這次登入的用戶端 ID 和 A 的雜湊值），把瀏覽器轉到這個用戶端登記的接收網址，Claude Code 在那裡收到它。 |
| ⑥ | 直接連線 | Claude Code 把授權碼、自己的用戶端 ID 和 A 的原值交給伺服器；三者都對得上，才換到 Claude Code token。 |

### 保護措施

- **偷到授權碼也換不到 token（PKCE）。** 隨機值 A、B 在網址上只出現雜湊值；原值只出現在
  第 ④⑥ 步的直接連線裡。授權方收到原值時算一次雜湊，和先前收到的對得上才發 token。所以在網址
  上看到授權碼的人，拿不到原值，也就換不到 token。第 ④ 步伺服器還要附上 client secret。
- **第 ③ 步只能在第 ② 步出發的那個瀏覽器完成。** 網址上的 state 要和瀏覽器裡的 cookie 對得上，
  伺服器才繼續；state 只能用一次，10 分鐘後失效。這是 OAuth 防「登入 CSRF」的標準做法。CSRF
  是跨站請求偽造：別的網站誘使你的瀏覽器送出你沒打算送的請求。用在登入上，就是誘使你的瀏覽器
  替別人完成一次登入。
- **第 ⑤ 步只會送到你電腦上的 localhost。** 註冊時登記的接收網址不是本機位址的用戶端，伺服器
  收到第 ① 步的登入請求時就拒絕。否則別人可以註冊一個接收網址指向自己伺服器的用戶端，騙你點它的
  登入連結，拿走讀你 Drive 的權限。這擋不住已經在你電腦上執行的惡意程式（見
  ARCHITECTURE.md）。

### 自動換新

伺服器只在 Claude Code 用 refresh token 換新時，才同時用 Google 的 refresh token 換一張新的
Google access token。所以伺服器讓 Claude Code 的 access token 比 Google 的早 5 分鐘過期：
只要 Claude Code 的 access token 還有效，伺服器手上的 Google access token 也一定有效。Claude
Code 下次呼叫伺服器時若發現自己的已過期，會先換新，所以你不必每小時重新登入。換新不會
延長 30 天的登入期限。

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
    Code token 解開，而伺服器只存 Claude Code token 的雜湊值。所以即使有人拿到 KV 裡的全部
    資料，也解不開 Google token。
  - 伺服器處理每次呼叫時，用 Claude Code token 解密，因此看得到 Google token 和檔案內容。
    所以你必須信任作者，就像使用其他由廠商代管的雲端服務一樣。
  - Google token 能讀你整個 Drive。伺服器只讀 Claude 在工具呼叫中指定的資料夾、它的子資料夾，
    以及其中捷徑指向的檔案與資料夾（連同那些資料夾的子資料夾），捷徑可以指向資料夾外；沒指定
    資料夾時讀範例資料夾。這只是程式的做法，不是安全邊界：檔案裡的文字也可能讓 Claude 改指定別的
    資料夾。
  - 授權資料以你的 Google 帳號 ID 當鍵、email 當標籤，兩者都是明文。Claude Code token 的開頭
    也帶著你的 Google 帳號 ID。
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
