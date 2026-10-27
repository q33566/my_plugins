/**
 * Sign-in backend for the Google Drive Reader plugin.
 *
 * Holds the Google OAuth client secret so it never ships with the plugin, and
 * stores nothing. Three routes:
 *   GET  /callback  Google redirects here; forward code/state/error to the
 *                   plugin's loopback listener (port carried inside `state`).
 *   POST /token     {code, code_verifier} -> Google token response.
 *   POST /refresh   {refresh_token}       -> Google token response.
 * The PKCE verifier stays in the plugin until /token, so a code seen in transit
 * is useless on its own.
 */
export interface Env {
  GOOGLE_CLIENT_ID: string;
  GOOGLE_CLIENT_SECRET: string;
  RATE_LIMITER?: { limit(options: { key: string }): Promise<{ success: boolean }> };
  /** Only for local end-to-end tests against a fake Google; unset in production. */
  GOOGLE_TOKEN_URL?: string;
}

const GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token";
const STATE_RE = /^(\d{4,5})\.[A-Za-z0-9_-]{20,64}$/;
const FORWARDED = ["code", "error", "state"];
const RETURNED = ["access_token", "expires_in", "refresh_token", "scope", "token_type", "error", "error_description"];
const MAX_FIELD = 2048;

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    try {
      if (url.pathname === "/callback") return callback(request, url);
      if (url.pathname === "/token") return await exchange(request, env, url, ["code", "code_verifier"], "authorization_code");
      if (url.pathname === "/refresh") return await exchange(request, env, url, ["refresh_token"], "refresh_token");
      if (url.pathname === "/") return page(HOME);
      if (url.pathname === "/privacy") return page(PRIVACY);
      return text(404, "Not found.");
    } catch {
      return json(502, { error: "backend_error", error_description: "The sign-in backend could not reach Google." });
    }
  },
};

function callback(request: Request, url: URL): Response {
  if (request.method !== "GET") return text(405, "Method not allowed.");
  const state = url.searchParams.get("state") ?? "";
  const port = Number(STATE_RE.exec(state)?.[1]);
  if (!port || port < 1024 || port > 65535) {
    return html(400, "This sign-in link is not valid. Start the sign-in again from Claude Code.");
  }
  const out = new URLSearchParams();
  for (const key of FORWARDED) {
    const value = url.searchParams.get(key);
    if (value !== null) out.set(key, value);
  }
  return new Response(null, {
    status: 302,
    headers: { location: `http://127.0.0.1:${port}/callback?${out}`, "cache-control": "no-store", "referrer-policy": "no-referrer" },
  });
}

async function exchange(request: Request, env: Env, url: URL, fields: string[], grantType: string): Promise<Response> {
  if (request.method !== "POST") return text(405, "Method not allowed.");
  if (!env.GOOGLE_CLIENT_ID || !env.GOOGLE_CLIENT_SECRET) {
    return json(500, { error: "server_misconfigured", error_description: "The backend has no Google client configured." });
  }
  if (env.RATE_LIMITER) {
    const key = request.headers.get("cf-connecting-ip") ?? "unknown";
    if (!(await env.RATE_LIMITER.limit({ key })).success) {
      return json(429, { error: "rate_limited", error_description: "Too many sign-in requests; wait a minute." });
    }
  }
  let body: Record<string, unknown>;
  try {
    body = (await request.json()) as Record<string, unknown>;
  } catch {
    return json(400, { error: "invalid_request", error_description: "Body must be JSON." });
  }
  const form = new URLSearchParams({ grant_type: grantType });
  for (const field of fields) {
    const value = body?.[field];
    if (typeof value !== "string" || !value || value.length > MAX_FIELD) {
      return json(400, { error: "invalid_request", error_description: `Missing or invalid ${field}.` });
    }
    form.set(field, value);
  }
  form.set("client_id", env.GOOGLE_CLIENT_ID);
  form.set("client_secret", env.GOOGLE_CLIENT_SECRET);
  if (grantType === "authorization_code") form.set("redirect_uri", `${url.origin}/callback`);
  const google = await fetch(env.GOOGLE_TOKEN_URL || GOOGLE_TOKEN_URL, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: form.toString(),
  });
  const reply = (await google.json().catch(() => ({}))) as Record<string, unknown>;
  const picked = Object.fromEntries(RETURNED.filter((k) => k in reply).map((k) => [k, reply[k]]));
  if (!google.ok && !("error" in picked)) {
    picked.error = "google_error";
    picked.error_description = `Google answered HTTP ${google.status} without details.`;
  }
  return json(google.status, picked);
}

// Google's consent screen needs a homepage and a privacy policy on the app's domain.
const REPO = "https://github.com/q33566/my_plugins";
const HOME = `<h1>Kurt Folder Reader</h1>
<p>The sign-in backend of a Claude Code plugin that reads one Google Drive folder (read-only).
It forwards Google's sign-in result to the plugin on your computer and adds the app's OAuth
client secret to token requests. Source and install steps: <a href="${REPO}">${REPO}</a>.</p>
<p><a href="/privacy">Privacy policy</a></p>`;
const PRIVACY = `<h1>Privacy policy — Kurt Folder Reader</h1>
<p><b>What the app accesses.</b> With your consent, the plugin requests the Google scope
<code>https://www.googleapis.com/auth/drive.readonly</code> and uses it only to list and read files
inside the one Drive folder configured in the plugin, when you ask Claude to. It never writes,
deletes or shares anything.</p>
<p><b>Where your data goes.</b> File contents go from Google Drive straight to the plugin on your
computer and from there to your Claude Code session. Your Google tokens are stored only on your
computer, in the plugin's data directory.</p>
<p><b>This backend</b> stores no tokens, files or personal data, and keeps no logs of them. It only
relays the sign-in redirect to your computer and passes token requests to Google with the app's
client secret added.</p>
<p><b>Removing access.</b> Uninstall the plugin (this deletes its stored tokens) and remove the app at
<a href="https://myaccount.google.com/permissions">myaccount.google.com/permissions</a>.</p>
<p><b>Contact.</b> Open an issue at <a href="${REPO}/issues">${REPO}/issues</a>.</p>`;

function page(body: string): Response {
  return new Response(`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">` +
    `<title>Kurt Folder Reader</title><body style="font-family:system-ui;max-width:42rem;margin:2rem auto;padding:0 1rem;line-height:1.5">${body}`, {
    headers: { "content-type": "text/html; charset=utf-8" },
  });
}

function json(status: number, body: unknown): Response {
  return Response.json(body, { status, headers: { "cache-control": "no-store" } });
}

function text(status: number, body: string): Response {
  return new Response(body, { status, headers: { "content-type": "text/plain; charset=utf-8" } });
}

function html(status: number, message: string): Response {
  return new Response(`<!doctype html><meta charset="utf-8"><title>Google Drive Reader</title><p>${message}</p>`, {
    status, headers: { "content-type": "text/html; charset=utf-8" },
  });
}
