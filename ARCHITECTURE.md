# Architecture

## Components

```
Claude Code ──stdio──▶ MCP server (Python, runs on the user's machine, started by uv)
                         │  drive_list_folder / drive_read_file / drive_auth_status
                         │  tokens: plugin data dir, token.json (0600)
                         ├──HTTPS──▶ Google Drive API v3 (read-only scope)
                         └──HTTPS──▶ Sign-in backend (Cloudflare Worker, stateless)
                                        holds the OAuth client secret
                                        └──HTTPS──▶ oauth2.googleapis.com/token
```

## Sign-in flow

1. A tool call finds no tokens. The server opens a one-shot listener on
   `127.0.0.1:<random port>`, makes a PKCE verifier (kept in memory) and a `state`
   of `<port>.<random>`, and builds Google's consent URL with
   `redirect_uri = <backend>/callback`.
2. The server asks Claude Code to open that URL (MCP URL elicitation: the user
   confirms first). The URL is also returned as text when the prompt is
   unavailable or declined.
3. After consent, Google redirects to `<backend>/callback`. The backend only
   checks that `state` carries a valid port and redirects the browser to
   `http://127.0.0.1:<port>/callback` with `code` and `state`. It stores nothing.
4. The listener checks `state`, then sends `code` + the PKCE verifier to
   `<backend>/token`, which adds the client secret and calls Google.
5. The tokens are written to `token.json` (atomic write, mode 0600). Later, an
   expired access token is refreshed through `<backend>/refresh`; Google's
   refresh reply has no new refresh token, so the stored one is kept.

## Choices and why

- **Local server, tokens on the user's machine.** The Google tokens never pass
  through or rest on a server we run. The cost is one prerequisite (uv) on the
  user's machine.
- **A backend only for the client secret.** Google's token endpoint requires
  the client secret even with PKCE. Shipping it inside the plugin would put it
  in a public repository; instead a stateless Worker adds it. The PKCE verifier
  never leaves the user's machine before the exchange, so a code seen in
  transit is useless alone. The Worker cannot stop someone who already holds a
  code and its verifier from using it: the secret can be rotated and the
  endpoints are rate-limited, but this is not secrecy against the user.
- **Authorization code + PKCE + `state`, loopback redirect.** Google's device
  flow does not allow Drive read scopes; out-of-band copy/paste is retired.
- **Scope `drive.readonly`.** It is the narrowest scope that can list a folder
  someone shared with you, including subfolders, and download its files.
  `drive.file` with the Picker does not cover a picked folder's children.
  Because the scope is "restricted", Google shows the unverified-app screen;
  verification needs an annual third-party security assessment, out of scope
  here.
- **Reads confined to the configured folder.** `drive.readonly` reaches the
  whole Drive, so the server only serves files reached by walking the
  configured folder (shortcut targets included). A prompt hidden in a file
  cannot make it read other files.
- **File types.** Google Docs/Slides export to text, Sheets to CSV (first
  sheet). PDFs: the text layer via `pypdf`; a PDF with no text (scanned) is
  returned as an embedded resource, which Claude Code saves to a file that
  Claude opens with Read (it reads the page images). Images return as MCP image
  blocks. Other types get a clear "cannot parse" message.
- **Tools load upfront** (`anthropic/alwaysLoad`) and the server instructions
  say when to use them, so "list the folder" works without a URL.

## Trade-offs and residual risks

- Sign-in and token refresh need the backend to be up.
- A Google Workspace admin can block unverified apps or restricted scopes.
- The unverified app is limited to 100 new users over its lifetime.
- uv's first start downloads packages; on a very slow network it can exceed
  Claude Code's 30 s server start timeout (fix: `/mcp` → Reconnect).
- Claude Code keeps copies of files it read (tool results) under
  `~/.claude/projects/` until its own cleanup.

## Rejected alternatives

- **Remote MCP server holding users' Google tokens** (Claude Code's built-in
  MCP OAuth): no local runtime, but our server would keep every user's refresh
  token, and on Cloudflare's free plan returning a few-MB file exceeds the
  10 ms CPU limit.
- **No backend, Desktop client secret shipped in the plugin**: simplest, and
  Google documents installed-app secrets as non-confidential, but it puts a
  secret in a public repository.
- **Service account / API key**: not the user's OAuth; the folder is shared
  with the user, not with a service account.
