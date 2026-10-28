# 開發時用到的 skill

作者開發這個 plugin 時用到的 Claude Code skill 副本（plugin 說明見
[README](../../README.md)）。它們不是 plugin 的一部分。要試用，把 skill 的資料夾複製到專案的
`.claude/skills/` 底下。

部分 skill 依賴這裡沒有的東西：`tdd` 會引用 `codebase-design` 和 `code-review`；
`adversarial-plan` 和 `adversarial-build` 裡的 `quota_wait.sh`（等待 Claude 用量額度恢復的
腳本）需要 `jq`（處理 JSON 的命令列工具），並讀取 `~/.claude/usage-limits.json`。這個 JSON 檔
由作者另一支記錄用量額度的腳本產生，那支腳本沒有附上。缺少 `jq` 或這個檔案時，
`quota_wait.sh` 會報錯結束；這兩個 skill 一開始就讓 Claude 在背景啟動它，它報錯結束時，skill
要 Claude 修好原因再重新啟動。所以這兩個 skill 無法照原樣使用：要用的話，得自己修改兩個資料夾裡的
`SKILL.md`、`grill-loop.md` 與 `progress.py`，拿掉監看用量的部分（包括 `progress.py` 對
checkpoint 裡 `watcher` 欄位的檢查）。

`handoff` 只能手動輸入 `/handoff` 叫用，Claude 不會自己觸發。

| Skill | 用途與在這個 plugin 的用法 |
|---|---|
| [adversarial-plan](adversarial-plan/SKILL.md) | 找出問題根因並規劃修改。每一輪派獨立的審查 agent 檢查根因與計劃，直到一整輪找不到阻擋問題為止；遇到只有使用者能決定的取捨，就分別審查每個選項，再由使用者挑選。用來規劃這個 plugin。 |
| [adversarial-build](adversarial-build/SKILL.md) | 照選定的計劃實作，再派獨立的審查 agent 一輪輪檢查程式碼，直到一整輪找不到阻擋問題、而且每項需求都有證據；若有獨立於程式的標準答案可對照，再依事先約定的驗收條件驗證，直到通過，或每個落差都有明確結論。這裡用來對照的是一個範例 Google Drive 資料夾，由作者手動測試。 |
| [tdd](tdd/SKILL.md) | 先寫測試再寫程式。透過 adversarial-build 用在 plugin 的程式碼上。 |
| [report-text-review](report-text-review/SKILL.md) | 派獨立的審查 agent 檢查文件的文字：先請沒有背景的審查 agent 看讀不讀得懂，再看哪些能刪，再看精準度與中文文字。用在 [README.md](../../README.md)、[ARCHITECTURE.md](../../ARCHITECTURE.md)，以及一份不在這個 repo 裡的設定手冊。 |
| [handoff](handoff/SKILL.md) | 寫交接文件，摘要一個 session 的工作，讓新的 session 接著做。用來把工作從網頁版 Claude Code 移到作者電腦上的 Claude Code。 |

`tdd` 和 `handoff` 來自 Matt Pocock 的 skill 集（<https://github.com/mattpocock/skills>），
其餘三個由作者撰寫。
