# Google Drive Reader — a Claude Code plugin

Ask Claude Code about the files in a Google Drive folder. This plugin is the
author's solution to a take-home assignment from OakMega. The plugin connects
Claude Code to a remote MCP server that the author runs on Cloudflare Workers.
(MCP, the Model Context Protocol, is how Claude Code calls outside tools.) The
server signs you in with Google OAuth and asks only for read-only Drive access.
It lists the files in the folder you name and in its subfolders, and reads
files: Google Docs, Sheets and Slides and text files come back as text, images
as images, and PDFs as files that Claude reads page by page, including scanned
PDFs. For other file types, Claude is told the file cannot be read.

The plugin cannot search Drive, write to Drive, or watch files for changes.

## Install

**Prerequisites**: Claude Code 2.1.310 or later (check with `claude --version`)
and a Google account that can open the folder you want to read. Nothing else to
install.

In Claude Code, add this repository as a plugin source (a "marketplace"), then
install the plugin, named `gdrive-reader`, from it:

```
/plugin marketplace add q33566/my_plugins
/plugin install gdrive-reader@gdrive-reader-marketplace
```

If Claude Code asks you to, run `/reload-plugins` (or restart Claude Code).

## Sign in (once)

1. Run `/mcp`, select gdrive-reader (it shows **needs authentication**) and
   choose **Authenticate**. Claude Code opens your browser.
2. Pick your Google account. Google shows **"Google hasn't verified this app"**
   because the author's OAuth app, Kurt-Folder-Reader, has not gone through
   Google's verification (see [ARCHITECTURE.md](ARCHITECTURE.md)). Click
   **Advanced**, then **Go to Kurt-Folder-Reader (unsafe)**.
3. Tick the checkbox **"See and download all your Google Drive files"**, then
   allow access. Google grants read access to your whole Drive; the server itself
   reads only files inside the requested folder and its subfolders.
4. The browser says authentication succeeded, and `/mcp` shows gdrive-reader as
   connected. From now on Claude Code renews the sign-in automatically, so you do
   not need to sign in again.

If you ask Claude about a folder before signing in, Claude tells you to sign in
and gives you the sign-in link.

## Use it

- Name a folder: **"List the files in https://drive.google.com/drive/folders/…"**.
  Any folder your Google account can open works.
- Without a folder, **"List the files in the Drive folder"** lists the default
  folder: the test folder OakMega shared for this take-home assignment (set as
  `DEFAULT_FOLDER` in `backend/wrangler.toml`). Your Google account needs access
  to it.
- Then ask about a file in the listed folder. For the test folder, for example:
  **"Read the PDF"** or **"What is in the image?"**.

## Where your tokens and data are stored

- **Claude Code** keeps the tokens this server issued to it (an access token and a
  refresh token) in the macOS keychain, or in Claude Code's credentials file on
  other systems.
- **The server** (`https://gdrive-reader-broker.gdrive-reader-broker.workers.dev`,
  the URL in `plugins/gdrive-reader/.mcp.json`) stores a record of your sign-in
  in Cloudflare Workers KV. That record holds your Google tokens in encrypted
  form; the decryption key is itself encrypted with the tokens Claude Code holds,
  so the data in Workers KV cannot be decrypted without them. The server stores
  those Claude Code tokens only as hashes. File contents pass through the server
  only while it answers a request; the server does not store or log them (see
  `backend/src/mcp.ts` and `backend/src/drive.ts`).
- **To sign out**: `/mcp` → gdrive-reader → **Clear authentication**, and remove
  Kurt-Folder-Reader at <https://myaccount.google.com/permissions>.

## Troubleshooting

| What you see | What to do |
|---|---|
| In `/mcp`, gdrive-reader shows **needs authentication** | Choose gdrive-reader → **Authenticate**. |
| The sign-in page says **"Google Drive read access was not granted"** | You left the Drive checkbox unticked on Google's page. Sign in again (`/mcp` → gdrive-reader → **Authenticate**) and tick the checkbox. |
| Google says **"Access blocked"** or that your admin has restricted the app | The admin of your Google Workspace (company or school) account blocks unverified third-party apps. Use a personal Gmail account that can open the folder. |
| Claude answers with a different Google Drive tool | Another Google Drive integration is enabled, such as the Google Drive connector from claude.ai. Turn it off in `/mcp`, or say **"Use the gdrive-reader plugin"**. |
| Claude says a file is **outside the folder** | The server reads only files inside the folder you named and its subfolders. Name the folder that contains the file. |
| Claude Code runs over SSH and the browser is on another machine | When you choose **Authenticate**, Claude Code also prints the sign-in URL; open it in the browser on your own machine and sign in. At the end the browser is sent to Claude Code's `localhost` page, which fails to load because Claude Code runs on the other machine. Copy that page's full URL from the address bar and paste it into your conversation with Claude; Claude uses that URL to finish the sign-in. |

## Run your own server (self-host)

You need a Google account, a Cloudflare account (the free plan is enough for
files within the limits listed in [ARCHITECTURE.md](ARCHITECTURE.md)), Node.js
with npm, and a clone of this repository.

1. **Google Cloud project**: <https://console.cloud.google.com/> → create a project →
   *APIs & Services* → enable **Google Drive API**.
2. **OAuth client**: in the Cloud Console, open *Google Auth Platform*. A new
   project first asks you to **Get started**: enter an app name without "Google"
   or other Google product names such as "Drive" (saving fails otherwise), a
   support email, audience **External**, and a contact email. Then *Clients* →
   *Create client* → type **Web application**. Save the client ID and client
   secret; step 3 uses them.
3. **Server** (Cloudflare Workers): your Worker URL will be
   `https://gdrive-reader-broker.<your-subdomain>.workers.dev`, where
   `<your-subdomain>` is your account's workers.dev subdomain (shown in the
   Cloudflare dashboard under *Workers & Pages*). Below, `<worker>` means that URL.
   ```
   cd backend
   npm install
   npx wrangler login
   npx wrangler kv namespace create OAUTH_KV
   ```
   In `backend/wrangler.toml`, set the `id` under `[[kv_namespaces]]` to the id
   that `npx wrangler kv namespace create OAUTH_KV` printed, and set
   `GOOGLE_CLIENT_ID` (the client ID from step 2), `DEFAULT_FOLDER` (a Drive
   folder URL) and `MCP_RESOURCE` (`<worker>/mcp`). Then deploy, and store the
   client secret (the second command asks you to paste it):
   ```
   npx wrangler deploy
   npx wrangler secret put GOOGLE_CLIENT_SECRET
   ```
4. **Back in Google Auth Platform**:
   - *Clients* → your client → add the authorized redirect URI `<worker>/callback`.
   - *Branding*: set the homepage `<worker>/`, privacy policy `<worker>/privacy`
     (the Worker serves both), and authorized domain
     `<your-subdomain>.workers.dev`.
   - *Data Access*: add the scope `https://www.googleapis.com/auth/drive.readonly`.
   - *Audience*: **Publish app** (the status becomes
     "In production"). Or keep **Testing** and add your account as a test user;
     a test user's sign-in expires after 7 days and must be repeated.
5. **Plugin**: set the `url` in `plugins/gdrive-reader/.mcp.json` to
   `<worker>/mcp`. If you installed the author's version, remove it first
   (`/plugin uninstall gdrive-reader@gdrive-reader-marketplace`, then
   `/plugin marketplace remove gdrive-reader-marketplace`). Then add your clone:
   ```
   /plugin marketplace add <path-to-your-clone>
   /plugin install gdrive-reader@gdrive-reader-marketplace
   ```

## Development

From the repository root:

```
(cd backend && npm install && npm test)   # end-to-end tests of the sign-in flow and tools, against simulated Google services
(cd backend && npx tsc --noEmit)          # type check
claude plugin validate --strict plugins/gdrive-reader
```

The first version ran the MCP server on your machine and kept the Google tokens
there; that version is tagged `v0.1.0-local-server`. See
[ARCHITECTURE.md](ARCHITECTURE.md) for why the server moved off your machine.
