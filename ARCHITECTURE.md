# 架構

這個外掛讓 Claude Code 列出並讀取使用者能開啟的 Google Drive 資料夾裡的檔案。它是 OakMega
回家作業的成品，預設資料夾就是作業的測試資料夾（一個 PDF、一張圖片）。所有使用者共用同一個
Worker，由作者架在 Cloudflare 的免費方案上。安裝與使用見 [README](README.md)，資料存放見 [docs/data-storage.md](docs/data-storage.md)，登入的
每一步見 [docs/oauth-flow.md](docs/oauth-flow.md)；這份文件說明元件、設計取捨與限制。

## 元件

```
Claude Code ──HTTPS（MCP，streamable HTTP）──▶ Cloudflare Worker  /mcp
  token 存在 Claude Code 的憑證儲存區           │  三個工具：drive_list_folder / drive_read_file / drive_auth_status
                                                │  給 Claude Code 用的 OAuth 伺服器（@cloudflare/workers-oauth-provider）
                                                │  Workers KV：註冊過的用戶端、授權資料（props 加密）、token 雜湊值
                                                ├──HTTPS──▶ Google OAuth（同意畫面、換發與更新 token）
                                                └──HTTPS──▶ Google Drive API v3（唯讀權限）
```

外掛本身只有一筆 `.mcp.json` 設定：`{"type": "http", "url": "<worker>/mcp"}`，`<worker>` 是
Worker 的網址。

下面用到的詞：圖中的 OAuth 函式庫下稱「provider」。每完成一次登入，就在 Workers KV 留一筆
**授權資料**（grant，和 docs/data-storage.md 同名）。授權資料帶著 **props**，也就是 Worker 附在上面、加密存放的資料；這裡的
props 是使用者的 Google token 與 email（email 另外以明文存成授權資料的標籤）。`beginUpstream()`、`finishUpstream()`、`completeAuthorization()` 與
`tokenExchangeCallback` 是 provider 的 API，「upstream」指 Google。三個工具：`drive_list_folder`
列出資料夾樹，`drive_read_file` 讀一個檔案，`drive_auth_status` 顯示登入的帳號。

## 登入流程

1. Claude Code 不帶 token 呼叫 `/mcp`，收到 `401` 與
   `WWW-Authenticate: Bearer resource_metadata=…`。它讀取受保護資源的中繼資料（RFC 9728）與
   授權伺服器的中繼資料（RFC 8414），再到 `/register` 註冊自己（RFC 7591）。
2. 使用者選 **Authenticate**，Claude Code 開啟 `/authorize`，帶著它的 PKCE challenge 與接收網址
   `http://localhost:<port>/callback`。
3. Worker 為 Google 產生自己的一組 PKCE，用 `beginUpstream()` 存下這次請求（它把 `state` 綁到
   這個瀏覽器的 cookie），再轉到 Google 的同意畫面，要求 `openid email drive.readonly`
   （`openid email` 用來辨識授權資料屬於哪個帳號），並帶 `access_type=offline` 與
   `prompt=consent`：Google 只在重新同意時才給 refresh token，所以每次登入（包括重複登入）都會拿到
   新的 refresh token。
4. Google 轉回 `<worker>/callback`。`finishUpstream()` 核對 `state` 與 cookie，Worker 用用戶端密鑰
   與 PKCE verifier 換 Google token，確認有 Drive 權限、也拿到 refresh token，再呼叫
   `completeAuthorization()`，把 Google token 存成授權資料的 props。瀏覽器轉回 Claude Code 的
   localhost 接收網址。
5. Claude Code 到 `/token` 用授權碼換 token。之後 Claude Code 每次換新時，
   `tokenExchangeCallback` 都會更新 Google access token 並存回授權資料（效期怎麼對齊見
   oauth-flow 的「自動換新」）。如果 Google 已撤銷授權，換新會以 `invalid_grant` 失敗，Claude Code
   會請使用者重新登入。

## 設計取捨

- **遠端 MCP 伺服器。** Claude Code 裡的代管連接器（Notion、Linear、claude.ai 的連接器）都這樣
  運作：不用安裝任何東西、從 `/mcp` 登入、token 由 Claude Code 保管與換新。舊版的做法見
  「放棄的方案」。
- **Google token 放在伺服器，每筆授權資料各自加密。** 加密方式、哪些資料是明文，以及為什麼使用者
  必須信任作者，見 [docs/data-storage.md](docs/data-storage.md)。
- **只接受本機接收網址，沒有用戶端核准頁。** 動態註冊（`/register`）讓任何人都能註冊用戶端，並
  自訂接收網址。像這個伺服器一樣、所有使用者共用同一個 Google OAuth 用戶端的伺服器，MCP Security
  Best Practices 要求加一頁用戶端核准頁：轉到 Google 之前，先請使用者核准這個註冊的用戶端。這裡
  改成只在用戶端的接收網址是 `http://` 本機位址（`localhost`、`127.0.0.1` 或 `[::1]`）時，
  `/authorize` 才繼續，讓授權碼只送到使用者自己電腦上的程式。這樣擋得住的攻擊與擋不住的情況，見
  oauth-flow 的「保護措施」。
- **無狀態 MCP 處理器**（`createMcpHandler`）：每個請求建立一個新的伺服器，不用 Durable Objects。
  工具不需要由伺服器主動傳訊息給用戶端。
- **權限 `drive.readonly`。** 能列出別人分享給你的資料夾（含子資料夾）並下載檔案的最小權限。
  `drive.file` 搭配 Picker 涵蓋不到所選資料夾底下的檔案。這個權限屬於「受限制」等級，所以 Google
  會顯示未審核應用程式的畫面；審核需要每年一次的第三方資安評估，不在這次範圍內。
- **資料夾每次請求自由指定。** 使用者在對話裡給資料夾（Drive 網址或 ID），Claude 把它當作工具的
  `folder` 參數傳入；沒給時，工具用 Worker 的 `DEFAULT_FOLDER`。資料夾是工具參數而不是外掛設定，
  因為每次請求的資料夾可能不同。
- **讀檔只限指定的資料夾（不是安全邊界）。** `drive.readonly` 能讀整個 Drive。為了讓每次讀取只限於
  這次請求的資料夾，`drive_read_file` 只在走訪指定資料夾（或預設資料夾）時找得到該檔案才回傳；捷徑
  指向的檔案也算在內。所以只有檔案 ID（例如 Claude 記得的另一個資料夾裡的檔案）不夠。這擋不住讀取
  別處：使用者可以指定任何資料夾，檔案裡要 Claude 讀別的東西的文字也能指定資料夾，包括整個「我的
  雲端硬碟」（`root`），共用資料夾裡的捷徑也能指向任何地方；帳號開得了的資料夾，設計上都讀得到。
- **PDF 以檔案回傳，不在伺服器解析。** 抽文字會超過免費方案每個請求 10 毫秒的 CPU 時間。PDF 以
  embedded resource 回傳，Claude Code 存成檔案，Claude 再用 `Read` 工具讀，它也會把頁面當圖片看。
  圖片以 MCP 圖片區塊回傳；Google 文件、試算表、簡報由 Drive 匯出成文字。

## 限制與殘留風險

README「使用」一節的上限已列出檔案大小、資料夾項目數，以及 100 位新使用者的上限。這裡補充原因
與其他限制：

- Cloudflare 免費方案：Claude Code 每呼叫一次伺服器，伺服器最多再發出 50 個子請求（含呼叫 Google
  和 Workers KV），CPU 時間最多 10 毫秒（等網路回應的時間不算）。每個子資料夾（所有層合計）至少要
  一個子請求，而且讀檔前會重列整個資料夾（確認檔案在指定的資料夾裡），因此子資料夾接近 50 個時，
  列出和讀檔都可能整個失敗，而不是只列出一部分；這通常比 2,000 個項目的上限先碰到。
- 測試檔（300 KB 圖片、600 KB PDF）編碼成 base64 文字回傳，在免費方案的限制內；幾 MB 的檔案或有幾十個
  子資料夾的資料夾，可能需要付費方案（每月 5 美元：30 秒 CPU、10,000 個子請求）。
- 下載上限是為了讓回應小於 Claude Code 的 16 MiB 上限；檔案太大時不讀，並告訴 Claude 檔案大小與
  上限。
- 登入完成 30 天後，授權資料和 provider 為它發的每張 refresh token 一起到期（provider 的預設效期，
  換新不會延長）。
- 沒有用戶端核准頁，仍有一種攻擊擋不住，見 oauth-flow 的「仍然擋不住的情況」。
- 登入與每次工具呼叫都需要 Worker 正常運作。
- Google Workspace 管理員可以封鎖未審核的應用程式或受限制的權限。

## 放棄的方案

- **本機 MCP 伺服器，token 存在使用者電腦**（v0.1.0）：Google token 不經過任何伺服器，但需要安裝
  uv（Python 工具安裝程式），而且由伺服器在第一次呼叫工具時自己開始登入，而不是用 Claude Code
  標準的 `/mcp` 流程。
- **改用 Google Cloud Run 而不是 Workers**：沒有每個請求 10 毫秒的 CPU 限制，但 Workers 用一個
  函式庫就有的 OAuth 伺服器（含加密的授權資料 props），得用其他元件自己組；另外還需要帳單帳戶，
  而且有冷啟動。
- **服務帳戶／API 金鑰**：不是使用者本人的 OAuth；資料夾是分享給使用者，不是分享給服務帳戶。
