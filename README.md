# Google Drive Reader — a Claude Code plugin

Ask Claude Code about the files in a Google Drive folder. The plugin connects
Claude Code to a remote MCP server that signs you in with Google (OAuth,
read-only), lists a folder with its subfolders, and reads files: Google
Docs/Sheets/Slides and text files as text, images as images, and PDFs as files
Claude reads page by page (scanned PDFs included).

It reads only. There is no search, write or change-watching.

## Install (about 1 minute)

**Prerequisites**: Claude Code 2.1.310 or later (`claude --version`) and a Google
account that can open the Drive folder. Nothing else to install.

In Claude Code:

```
/plugin marketplace add q33566/my_plugins
/plugin install gdrive-reader@gdrive-reader-marketplace
```

If Claude Code asks you to, run `/reload-plugins` (or restart Claude Code).

## Sign in (once)

1. Run **`/mcp`**, select **gdrive-reader** (it shows "needs authentication") and
   choose **Authenticate**. Claude Code opens your browser.
2. Pick your Google account. Google shows **"Google hasn't verified this app"**:
   click **Advanced**, then **Go to Kurt-Folder-Reader (unsafe)** (the name of
   the author's OAuth app).
3. Allow both items, including **"See and download all your Google Drive
   files"** (the server only ever reads, and only inside the folder you ask about).
4. The browser says authentication succeeded; `/mcp` now shows gdrive-reader as
   connected. Claude Code renews the sign-in by itself from now on.

If you ask Claude about the folder before signing in, Claude tells you to sign in
and gives you the same link.

## Use it

- **"List the files in the Drive folder."** Without a folder, it uses the
  take-home test folder.
- **"Read the PDF"**, **"What is in the image?"**, or any question about a file.
- Another folder: **"List the files in https://drive.google.com/drive/folders/…"**.

## Where your sign-in is stored

- **Claude Code** keeps its token for this server in your system keychain
  (macOS) or its credentials file.
- **The server** keeps your Google tokens inside the sign-in grant, encrypted
  with a key that only the token held by your Claude Code unlocks; its storage
  holds only hashes of tokens. File contents pass through the server only while
  answering a request and are never stored or logged.
- **To sign out**: `/mcp` → gdrive-reader → **Clear authentication**, and remove
  the app at <https://myaccount.google.com/permissions>.

## Troubleshooting

| What you see | What to do |
|---|---|
| gdrive-reader shows **needs authentication** | Run `/mcp` → gdrive-reader → **Authenticate**. |
| Sign-in says **"Google Drive read access was not granted"** | You left the Drive checkbox unticked on Google's page. Authenticate again and tick it. |
| Google says **"Access blocked"** or the admin restricted the app | Your Google Workspace admin blocks unverified third-party apps. Use a personal Gmail account that can open the folder. |
| Claude answers with a different Google Drive tool | You have the claude.ai Google Drive connector on. Turn it off in `/mcp`, or say "use the gdrive-reader plugin". |
| "outside the folder" | Only files inside the folder you asked about (and its subfolders) can be read. |
| Claude Code runs over SSH and the browser is on another machine | After you allow access, the browser fails to open a `localhost` page. Copy that page's full URL from the address bar and give it to Claude, which finishes the sign-in with it. |

## Run it on your own Google Cloud project (self-host)

1. **Google Cloud project**: <https://console.cloud.google.com/> → create a project →
   *APIs & Services* → enable **Google Drive API**.
2. **OAuth client**: *Google Auth Platform* → *Clients* → *Create client* → type
   **Web application**. Keep the client ID and secret.
3. **Server** (Cloudflare Workers; the free plan handles files of a few MB):
   ```
   cd backend
   npm install
   npx wrangler login
   npx wrangler kv namespace create OAUTH_KV   # put the printed id in wrangler.toml
   ```
   In `backend/wrangler.toml` set `GOOGLE_CLIENT_ID`, `DEFAULT_FOLDER`, and
   `MCP_RESOURCE` (`https://<worker>/mcp`, the URL you will deploy to). Then:
   ```
   npx wrangler deploy
   npx wrangler secret put GOOGLE_CLIENT_SECRET
   ```
4. **Back in Google Auth Platform** (`<worker>` is your Worker URL):
   - *Clients* → your client → authorized redirect URI `<worker>/callback`.
   - *Branding*: an app name without "Google" or other Google product names such
     as "Drive" (saving fails otherwise), support email, homepage `<worker>/`,
     privacy policy `<worker>/privacy` (the Worker serves both), authorized
     domain `<your-subdomain>.workers.dev`.
   - *Data Access*: add the scope `https://www.googleapis.com/auth/drive.readonly`.
   - *Audience*: user type **External**, then **Publish app** (In production).
     Or keep *Testing* and add your account as a test user — test-user sign-ins
     expire after 7 days.
5. **Plugin**: set the `url` in `plugins/gdrive-reader/.mcp.json` to
   `<worker>/mcp` and install the plugin from your copy of the repository.

## Development

```
cd backend && npm install && npm test     # sign-in flow and tools end to end, with a fake Google
npx tsc --noEmit                          # type check
claude plugin validate --strict plugins/gdrive-reader
```

The first version ran the MCP server locally (Python, started by uv) and kept the
tokens on the user's machine; it is tagged `v0.1.0-local-server`. See
[ARCHITECTURE.md](ARCHITECTURE.md) for why it moved to a remote server.
