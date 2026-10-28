# Token 與資料存放

這份文件說明 Google Drive Reader 在你的電腦和作者的伺服器上各存了什麼、怎麼保護，以及怎麼登出。
安裝與使用見 [README](../README.md)，登入怎麼運作見 [oauth-flow.md](oauth-flow.md)。

登入會產生兩組 token（有權限範圍、會過期的存取憑證）：

- **Google token**：Google 發給伺服器，伺服器用它讀你的 Drive。
- **Claude Code token**：伺服器發給 Claude Code，Claude Code 每次呼叫伺服器都帶著它。

每組都有一張用來存取、約一小時過期的 **access token**，和一張只用來換新 access token 的
**refresh token**。

## 你的電腦

- Claude Code 存兩張 Claude Code token：access token，和登入完成後 30 天內有效的 refresh token。
  macOS 存在鑰匙圈，其他系統存在 Claude Code 的憑證檔。
- refresh token 被偷的話，偷的人能透過伺服器讀你的 Drive，最長到登入滿 30 天，所以要和密碼一樣
  保護。
- Claude Code 會把讀過的檔案內容留在 `~/.claude/projects/` 底下，直到 Claude Code 自行清理。
- Google token 不會存在你的電腦，Claude Code 拿不到。

## 伺服器

伺服器在 Cloudflare Workers KV（Cloudflare 的鍵值儲存）存一筆你的授權資料。

- 授權資料裡有 Google 的 access token 和 refresh token，都是加密的；解密金鑰只能用 Claude Code
  token 解開。登入途中，Claude Code 換到 token 之前的幾分鐘內，金鑰改用授權碼保護；授權碼是登入時
  伺服器發給 Claude Code、只能用一次來換 token 的短效代碼。伺服器只存 Claude Code token 與授權碼
  的雜湊值（單向運算的結果，推不回原值），所以即使有人拿到 Workers KV 裡的全部資料，也解不開
  Google token。
- 伺服器處理每次呼叫時，用 Claude Code token 解密，因此看得到 Google token 和檔案內容。所以你必須
  信任作者，就像使用其他由廠商代管的雲端服務一樣。
- 授權資料以你的 Google 帳號 ID 當鍵、email 當標籤，兩者都是明文。Claude Code token 和登入時經
  瀏覽器網址傳給 Claude Code 的授權碼，開頭也都帶著你的 Google 帳號 ID。
- 檔案內容只在處理呼叫時經過伺服器，不儲存也不記錄；Google token 也不記錄。

## 讀取範圍

Google token 能讀你整個 Drive，伺服器只讀 README 開頭所說的範圍：指定的資料夾、它的子資料夾，
以及其中捷徑指向的檔案與資料夾。這只是程式的做法，擋不住讀取別的資料夾：Claude 會照檔案裡的
文字行事，所以檔案裡若寫著「去讀某某資料夾」，Claude 可能照做，而你的帳號開得了的資料夾都讀得到。

## 登出

1. 在 Claude Code 執行 `/mcp` → gdrive-reader → **Clear authentication**。
2. 到 <https://myaccount.google.com/permissions> 移除 Kurt-Folder-Reader。

移除後，授權資料裡的 Google token 就失效；授權資料本身最晚在登入完成滿 30 天時到期。
