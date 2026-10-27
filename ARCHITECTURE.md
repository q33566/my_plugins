# Architecture

## Components

```
Claude Code ──HTTPS (MCP, streamable HTTP)──▶ Cloudflare Worker  /mcp
  token in the system keychain                  │  drive_list_folder / drive_read_file / drive_auth_status
                                                │  OAuth server for Claude Code (@cloudflare/workers-oauth-provider)
                                                │  KV: registered clients, grants (props encrypted), token hashes
                                                ├──HTTPS──▶ Google OAuth (consent, token, refresh)
                                                └──HTTPS──▶ Google Drive API v3 (read-only scope)
```

The plugin itself is one `.mcp.json` entry: `{"type": "http", "url": "<worker>/mcp"}`.

## Sign-in flow

1. Claude Code calls `/mcp` without a token and gets `401` with
   `WWW-Authenticate: Bearer resource_metadata=…`. It reads the protected
   resource metadata (RFC 9728) and the authorization server metadata (RFC 8414),
   registers itself at `/register` (RFC 7591), and shows the server as "needs
   authentication" in `/mcp`.
2. The user chooses **Authenticate**. Claude Code opens `/authorize` with its PKCE
   challenge and its `http://localhost:<port>/callback` redirect.
3. The Worker refuses any client whose redirect is not a loopback address, makes
   its own PKCE pair for Google, saves the request with `beginUpstream()` (which
   binds the `state` to a cookie in this browser), and redirects to Google's
   consent screen asking for `openid email drive.readonly`, `access_type=offline`.
4. Google redirects to `<worker>/callback`. `finishUpstream()` checks `state`
   against the cookie (once only), the Worker exchanges the code with its client
   secret and verifier, checks that the Drive scope was granted and a refresh
   token came back, and calls `completeAuthorization()` with the Google tokens as
   the grant's props. The browser goes back to Claude Code's localhost callback.
5. Claude Code exchanges its code at `/token`. Its access token expires five
   minutes before Google's; when Claude Code refreshes, `tokenExchangeCallback`
   renews the Google access token and stores it in the grant. If Google has
   revoked the sign-in, the refresh fails with `invalid_grant` and Claude Code
   asks the user to authenticate again.

## Choices and why

- **Remote MCP server.** This is how hosted connectors (Notion, Linear, the
  claude.ai connectors) work in Claude Code: nothing to install, sign-in from
  `/mcp`, tokens kept and refreshed by Claude Code. The first version ran a local
  Python server and kept Google tokens on the user's machine; it needed uv and
  started the sign-in at the first tool call, which is not what users expect.
- **Google tokens on the server, encrypted per grant.** The provider stores
  tokens only as hashes and encrypts each grant's props with a key wrapped by the
  token Claude Code holds, so a copy of KV alone does not reveal Google tokens.
  The Worker can still use them while serving a request; that is the trust users
  give any hosted connector.
- **Only loopback redirects.** Dynamic registration lets anyone register a
  client. Refusing non-loopback redirects means a sign-in link sent by someone
  else cannot deliver the user's grant to that person's server, so no extra
  approval page is needed.
- **Stateless MCP handler** (`createMcpHandler`): a fresh server per request, no
  Durable Objects. The tools need no server-to-client messages.
- **Scope `drive.readonly`.** The narrowest scope that can list a folder someone
  shared with you, including subfolders, and download its files. `drive.file`
  with the Picker does not cover a picked folder's children. Because the scope is
  "restricted", Google shows the unverified-app screen; verification needs an
  annual third-party security assessment, out of scope here.
- **Reads confined to the folder.** `drive.readonly` reaches the whole Drive, so
  `drive_read_file` only serves files found by walking the folder (the default
  one, or the one Claude names). Plugin settings cannot reach a remote server, so
  the folder is a tool argument with a default instead of a setting.
- **PDFs as files, not parsed on the server.** Text extraction would exceed the
  free plan's 10 ms CPU per request. The PDF is returned as an embedded resource;
  Claude Code saves it and Claude reads it with Read, which also shows the pages
  as images (scanned PDFs work the same way). Images return as MCP image blocks;
  Google Docs/Sheets/Slides are exported as text by Drive.

## Limits and residual risks

- Free plan: 10 ms CPU and 50 subrequests per request. Base64-encoding the test
  files (300 KB image, 600 KB PDF) fits; files of many MB, or folders with dozens
  of subfolders, may need the paid plan ($5/month: 30 s CPU, 10,000 subrequests).
  Downloads are capped at 10 MB (5 MB for images) to stay under Claude Code's
  16 MiB response limit.
- Sign-in and every tool call need the Worker to be up.
- A Google Workspace admin can block unverified apps or restricted scopes.
- The unverified app is limited to 100 new users over its lifetime.
- Over SSH the browser cannot reach Claude Code's localhost callback; the user
  pastes the callback URL back to Claude instead.
- Claude Code keeps copies of files it read (tool results) under
  `~/.claude/projects/` until its own cleanup.

## Rejected alternatives

- **Local MCP server with tokens on the user's machine** (v0.1.0): keeps Google
  tokens off any server, but needs uv and a sign-in at the first tool call
  instead of Claude Code's standard `/mcp` flow.
- **Google Cloud Run instead of Workers**: no CPU limit, but no ready OAuth
  provider library for MCP, a billing account, and cold starts.
- **Service account / API key**: not the user's OAuth; the folder is shared with
  the user, not with a service account.
