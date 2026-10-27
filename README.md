# Google Drive Reader — a Claude Code plugin

Ask Claude Code about the files in one Google Drive folder. The plugin signs you
in with Google (OAuth, read-only), lists the folder with its subfolders, and reads
files: Google Docs/Sheets/Slides and text files as text, images as images, PDFs as
text (or, for scanned PDFs, as a file Claude reads page by page).

It reads only. There is no search, write or change-watching.

## Install (about 2 minutes)

**Prerequisites**

- Claude Code 2.1.310 or later (`claude --version`).
- [uv](https://docs.astral.sh/uv/getting-started/installation/), which runs the
  plugin's Python server and downloads the Python it needs:
  - macOS / Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
  - Windows (PowerShell): `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
- Then, once: `uv python install 3.12` (so the plugin's first start does not have
  to download Python).
- A Google account that can open the Drive folder.

**Install the plugin** — in Claude Code:

```
/plugin marketplace add q33566/my_plugins
/plugin install gdrive-reader@gdrive-reader-marketplace
```

If Claude Code asks you to, run `/reload-plugins` (or restart Claude Code). The
install may say that four options are "not yet set": that is expected. Their
defaults already point at the take-home test folder and the author's sign-in
backend, so there is nothing to configure.

## Use it

1. Ask: **"List the files in the Drive folder."**
2. The first time, Claude Code asks whether to open a Google sign-in link. Accept.
   (If you see the link as text instead, open it yourself.)
3. In the browser, pick your Google account. Google shows **"Google hasn't
   verified this app"**: click **Advanced**, then **Go to Kurt Folder Reader
   (unsafe)** (the name of the author's OAuth app), then allow **"See and
   download all your Google Drive files"**
   (the plugin only ever reads, and only inside the configured folder).
4. The browser says "Signed in"; Claude lists the folder. (If the link came as
   text in step 2, ask again after signing in.)
5. Ask: **"Read the PDF"**, **"What is in the image?"**, or any question about a file.

To check who is signed in, ask Claude to run `drive_auth_status`.

## Where your sign-in is stored

- Google tokens are saved on your machine in the plugin's data directory
  (`~/.claude/plugins/data/gdrive-reader-gdrive-reader-marketplace/token.json`),
  readable only by you (mode 0600 on macOS/Linux; on Windows it is protected by
  your user profile's permissions). They are never written to the repository and
  never logged.
- The sign-in backend stores nothing; it only adds the OAuth client secret to the
  token requests (see [ARCHITECTURE.md](ARCHITECTURE.md)).
- To sign out: delete that `token.json`, and remove the app's access at
  <https://myaccount.google.com/permissions>. Uninstalling the plugin deletes the
  data directory too.

## Troubleshooting

| What you see | What to do |
|---|---|
| `/mcp` shows **gdrive-reader** as failed | Usually uv was still downloading on first start. Run `uv python install 3.12`, then `/mcp` → gdrive-reader → **Reconnect**. Check `uv --version` works in a new terminal. |
| Google says **"Access blocked"** or the admin has restricted the app | Your Google Workspace admin blocks unverified third-party apps. Use a personal Gmail account that has access to the folder. |
| Claude answers with a different Google Drive tool | You have the claude.ai Google Drive connector enabled. Turn it off in `/mcp`, or say "use the gdrive-reader plugin". |
| "outside the configured folder" | The plugin only reads files inside the configured folder and its subfolders. |
| Claude Code runs over SSH and the browser is on another machine | Set the `callback_port` option (for example `53682`) in `/plugin` → gdrive-reader → configure, and forward it: `ssh -L 53682:127.0.0.1:53682 <host>`. |
| "The sign-in link expired" | Links are valid for 10 minutes; ask again and a new link is created. |

## Run it against your own Google Cloud project (self-host)

Everything the plugin uses can be replaced with your own:

1. **Google Cloud project**: <https://console.cloud.google.com/> → create a project →
   *APIs & Services* → enable **Google Drive API**.
2. **OAuth client**: *Google Auth Platform* → *Clients* → *Create client* → type
   **Web application**. Copy the client ID and secret (add the redirect URI in
   step 4).
3. **Backend** (Cloudflare Workers, free plan is enough): set `GOOGLE_CLIENT_ID`
   in `backend/wrangler.toml`, then
   ```
   cd backend
   npm install
   npx wrangler login
   npx wrangler deploy          # prints https://gdrive-reader-broker.<your-subdomain>.workers.dev
   npx wrangler secret put GOOGLE_CLIENT_SECRET
   ```
4. **Back in Google Auth Platform** (`<worker>` is the URL from step 3):
   - *Clients* → your client → authorized redirect URI `<worker>/callback`.
   - *Branding*: an app name without "Google" or other Google product names such
     as "Drive" (saving fails otherwise), support email, homepage `<worker>/`,
     privacy policy `<worker>/privacy` (the Worker serves both), authorized
     domain `<your-subdomain>.workers.dev`.
   - *Data Access*: add the scope `https://www.googleapis.com/auth/drive.readonly`.
   - *Audience*: user type **External**, then **Publish app** (In production).
     Or keep *Testing* and add your account as a test user — test-user sign-ins
     expire after 7 days.
5. **Plugin**: `/plugin` → gdrive-reader → configure: set `broker_url` to your
   Worker URL and `client_id` to your client ID (and `folder_url` to any folder),
   then restart Claude Code so the server starts with the new settings.

## Development

```
cd plugins/gdrive-reader && uv run pytest      # MCP server, sign-in, Drive logic (fakes for Google)
cd backend && npm install && npm test          # sign-in backend
claude plugin validate --strict plugins/gdrive-reader
```
