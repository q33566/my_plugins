# Architecture

The plugin lets Claude Code list and read files in a Google Drive folder the
user can open. It was written for a take-home assignment from OakMega, whose
test folder (one PDF and one image) is the default folder. One Worker, run by
the author on Cloudflare's free plan, serves every user.

## Components

```
Claude Code ──HTTPS (MCP, streamable HTTP)──▶ Cloudflare Worker  /mcp
  tokens in Claude Code's credential store      │  drive_list_folder / drive_read_file / drive_auth_status
                                                │  OAuth server for Claude Code (@cloudflare/workers-oauth-provider)
                                                │  KV: registered clients, grants (props encrypted), token hashes
                                                ├──HTTPS──▶ Google OAuth (consent, token, refresh)
                                                └──HTTPS──▶ Google Drive API v3 (read-only scope)
```

The plugin is one `.mcp.json` entry: `{"type": "http", "url": "<worker>/mcp"}`,
where `<worker>` is the Worker's URL.

Terms used below: the Worker is the OAuth server for Claude Code, built with the
`@cloudflare/workers-oauth-provider` library ("the provider"). Each completed
sign-in is a **grant**, a record in Workers KV (Cloudflare's key-value store).
A grant carries **props**, data the Worker attaches to it; here the props are the
user's Google tokens and email (the email is also kept unencrypted as the
grant's label, see below). `beginUpstream()`, `finishUpstream()`,
`completeAuthorization()` and `tokenExchangeCallback` are the provider's API;
"upstream" means Google. The tools: `drive_list_folder` lists a folder tree,
`drive_read_file` reads one file, `drive_auth_status` shows the signed-in
account.

## Sign-in flow

1. Claude Code calls `/mcp` without a token and gets `401` with
   `WWW-Authenticate: Bearer resource_metadata=…`. It reads the protected
   resource metadata (RFC 9728) and the authorization server metadata (RFC 8414),
   registers itself at `/register` (RFC 7591), and shows the server as "needs
   authentication" in the `/mcp` command.
2. The user chooses **Authenticate**. Claude Code opens `/authorize` with its PKCE
   challenge and its `http://localhost:<port>/callback` redirect.
3. The Worker refuses any client whose redirect is not a loopback address, makes
   its own PKCE pair for Google, saves the request with `beginUpstream()` (which
   binds the `state` to a cookie in this browser), and redirects to Google's
   consent screen asking for `openid email drive.readonly` (`openid email`
   identifies the account for the grant), with `access_type=offline` and
   `prompt=consent`: Google sends a refresh token only on a fresh consent, so
   every sign-in, including a repeat one, gets a new refresh token.
4. Google redirects to `<worker>/callback`. `finishUpstream()` checks `state`
   against the cookie (a `state` works only once), the Worker exchanges the code with its client
   secret and verifier, checks that the Drive scope was granted and a refresh
   token came back, and calls `completeAuthorization()` with the Google tokens as
   the grant's props. The browser goes back to Claude Code's localhost callback.
5. Claude Code exchanges its code at `/token`. The access token the Worker
   issues expires five minutes before Google's, so Claude Code normally refreshes
   before the Google token runs out (not if steps 4 to 6 of the first sign-in
   take more than five minutes); when Claude Code refreshes, `tokenExchangeCallback`
   renews the Google access token and stores it in the grant. If Google has
   revoked the sign-in, the refresh fails with `invalid_grant` and Claude Code
   asks the user to authenticate again.

## Choices and why

- **Remote MCP server.** This is how hosted connectors (Notion, Linear, the
  claude.ai connectors) work in Claude Code: nothing to install, sign-in from
  `/mcp`, tokens kept and refreshed by Claude Code. The first version ran a local
  Python server and kept Google tokens on the user's machine; it needed uv (a
  Python tool installer), and the server itself started the sign-in at the first
  tool call.
- **Google tokens on the server, encrypted per grant.** The tokens the provider
  issues to Claude Code are stored only as hashes. The Google tokens sit in the
  grant's props, encrypted with a key that is itself wrapped by the tokens Claude
  Code holds, so a copy of KV alone does not reveal Google tokens. It does reveal who signed
  in: the grant is stored under the Google account id and keeps the email as a
  plain-text label; only the props are encrypted. While serving a
  request the Worker does decrypt them, and it sees the file contents it returns;
  it stores the Google tokens only in that encrypted form, does not store file
  contents, and logs neither. Users therefore have to trust the
  operator, as with any hosted connector; here the operator is the author.
- **Only loopback redirects, no client-approval page.** Dynamic registration
  (`/register`) lets anyone register a client with a redirect of their
  choosing. An attacker could register a client whose redirect is the
  attacker's own server and send the user a sign-in link (an `/authorize` URL)
  for that client. Google's consent screen names only the Worker's app,
  Kurt-Folder-Reader, so nothing there shows who registered the client. The
  authorization code that the Worker issues would then go to the attacker's
  server, which could exchange it at `/token` for Worker tokens and read the
  user's Drive through the tools. The MCP Security Best Practices require a
  client-approval page for servers like this one, which use one OAuth client at
  Google for all users: before sending the user to Google, the server asks the
  user to approve the registered client. Instead, `/authorize` continues only
  when the client's redirect is an `http://` loopback address (`localhost`,
  `127.0.0.1` or `[::1]`), so the code goes to a program on the user's own
  machine (see "Limits and residual risks").
- **Stateless MCP handler** (`createMcpHandler`): a fresh server per request, no
  Durable Objects. The tools need no server-to-client messages.
- **Scope `drive.readonly`.** The narrowest scope that can list a folder someone
  shared with you, including subfolders, and download its files. `drive.file`
  with the Picker does not cover a picked folder's children. Because the scope is
  "restricted", Google shows the unverified-app screen; verification needs an
  annual third-party security assessment, out of scope here.
- **Any folder, chosen per request.** The user names the folder (a Drive URL or
  id) in the conversation, and Claude passes it to the tools as the `folder`
  argument; with no folder named, the tools use the Worker's `DEFAULT_FOLDER` (the take-home
  test folder on the author's server). The
  folder is a tool argument rather than a plugin setting because it can change
  from one request to the next.
- **File reads limited to the named folder (not a security boundary).**
  `drive.readonly` reaches the whole Drive. To keep each read to the folder the
  request is about, `drive_read_file` serves a file only if walking the named
  folder (or the default folder) finds it; shortcut targets count as inside. A
  file id alone, for example one Claude remembered from another folder, is
  therefore not enough. This does not stop reads elsewhere: the user can name any folder, and text
  inside a file that asks Claude to read something else can name a folder too,
  including the whole My Drive (`root`), and a shortcut in a shared folder can
  point anywhere; any folder the account can open is readable by design.
- **PDFs as files, not parsed on the server.** Text extraction would exceed the
  free plan's 10 ms CPU per request. The PDF is returned as an embedded resource;
  Claude Code saves it and Claude reads it with its `Read` tool, which also shows the pages
  as images (scanned PDFs work the same way). Images return as MCP image blocks;
  Google Docs/Sheets/Slides are exported as text by Drive.

## Limits and residual risks

- Free plan: 10 ms CPU and 50 subrequests per request. Base64-encoding the test
  files (300 KB image, 600 KB PDF) fits; files of several MB, or folders with dozens
  of subfolders, may need the paid plan ($5/month: 30 s CPU, 10,000 subrequests).
  Every file read walks the whole folder again, so a large folder makes reads
  fail too, not only listings. A listing also stops at 2,000 entries or 20
  levels; a file past that cut is reported as outside the folder.
  Downloads are capped at 10 MB (5 MB for images) to stay under Claude Code's
  16 MiB response limit; a larger file is not read, and Claude is told its size
  and the limit.
- Claude Code stays signed in for 30 days from the end of sign-in: the grant,
  and every refresh token the provider issues for it, expires then (the
  provider's default lifetime; refreshes do not extend it). After that the user
  signs in again.
- Without the client-approval page, one attack remains. An attacker can
  register a client with a loopback redirect and get the user to open its
  sign-in link; the state cookie is then set in the user's own browser, so
  that check passes, and the code lands on the user's machine. The attacker
  gets it, and with their PKCE verifier exchanges it for Worker tokens that read
  the user's Drive, if a program of theirs already listens on that loopback
  port, or if the user is tricked into sending them the final localhost URL
  from the address bar.
- Sign-in and every tool call need the Worker to be up.
- A Google Workspace admin can block unverified apps or restricted scopes.
- The unverified app is limited to 100 new users over its lifetime.
- Over SSH the browser cannot reach Claude Code's localhost callback; the user
  pastes the callback URL into the conversation in Claude Code instead.
- Claude Code keeps copies of files it read (tool results) under
  `~/.claude/projects/` until its own cleanup.

## Rejected alternatives

- **Local MCP server with tokens on the user's machine** (v0.1.0): keeps Google
  tokens off any server, but needs uv, and the server starts the sign-in
  itself at the first tool call instead of using Claude Code's standard `/mcp`
  flow.
- **Google Cloud Run instead of Workers**: no 10 ms CPU limit per request, but
  the OAuth server with encrypted grant props, which Workers gets from one
  library, would have to be assembled from other parts; it also needs a billing
  account and has cold starts.
- **Service account / API key**: not the user's OAuth; the folder is shared with
  the user, not with a service account.
