/**
 * Google Drive Reader: a remote MCP server on Cloudflare Workers.
 *
 * Claude Code connects to /mcp and signs in through the standard MCP authorization flow
 * (Claude Code registers itself, then the user signs in once from /mcp). This Worker is the
 * OAuth server for Claude Code and an OAuth client of Google:
 *   GET /authorize  start of Claude Code's sign-in: send the browser to Google's consent screen
 *   GET /callback   Google's redirect: swap its code for Google tokens, then finish Claude Code's sign-in
 *   /token, /register and the metadata endpoints are served by @cloudflare/workers-oauth-provider.
 * The Google tokens live only inside the grant, which the provider stores encrypted with a key
 * derived from the token Claude Code holds; KV keeps hashes, never the tokens themselves.
 */
import OAuthProvider, {
  AuthorizationError,
  authorizationErrorRedirect,
  OAuthError,
  type OAuthHelpers,
  type TokenExchangeCallbackOptions,
} from "@cloudflare/workers-oauth-provider";
import { createMcpHandler } from "agents/mcp/server";
import { consentUrl, DRIVE_SCOPE, exchangeCode, GoogleTokenError, pkce, refresh, userEmail } from "./google";
import { createServer, type Props } from "./mcp";

export interface Env {
  GOOGLE_CLIENT_ID: string;
  GOOGLE_CLIENT_SECRET: string;
  /** The folder the tools read when Claude passes none. */
  DEFAULT_FOLDER: string;
  /** The exact URL Claude Code connects to; every token is bound to it. */
  MCP_RESOURCE: string;
  OAUTH_KV: KVNamespace;
  OAUTH_PROVIDER: OAuthHelpers;
}

// Expire Claude Code's access token a little before Google's, so Claude Code refreshes first.
const EARLY = 300;
const LOOPBACK = new Set(["localhost", "127.0.0.1", "[::1]"]);

let provider: OAuthProvider<Env> | undefined;

export default {
  fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    provider ??= new OAuthProvider<Env>({
      apiRoute: "/mcp",
      apiHandler: { fetch: (req: Request, e: Env, c: ExecutionContext) => createMcpHandler(createServer(e.DEFAULT_FOLDER), { allowedHostnames: [new URL(e.MCP_RESOURCE).hostname] })(req, e, c) },
      authorizeEndpoint: "/authorize",
      tokenEndpoint: "/token",
      clientRegistrationEndpoint: "/register",
      defaultHandler: { fetch: (req: Request, e: Env) => site(req, e) },
      resourceMetadata: { resource: env.MCP_RESOURCE },
      tokenExchangeCallback: renewGoogle,
    } as ConstructorParameters<typeof OAuthProvider<Env>>[0]);
    return provider.fetch(request, env, ctx);
  },
};

async function site(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  try {
    if (url.pathname === "/authorize" && request.method === "GET") return await authorize(request, env, url);
    if (url.pathname === "/callback" && request.method === "GET") return await callback(request, env, url);
  } catch (e) {
    if (e instanceof AuthorizationError) {
      return e.redirectTo ? redirect(e.redirectTo) : page(400, `<p>Sign-in failed: ${escape(e.description)}</p><p>Start it again from Claude Code (<code>/mcp</code>).</p>`);
    }
    if (e instanceof GoogleTokenError) return page(502, `<p>${escape(e.message)}</p><p>Start the sign-in again from Claude Code (<code>/mcp</code>).</p>`);
    throw e;
  }
  if (url.pathname === "/") return page(200, HOME);
  if (url.pathname === "/privacy") return page(200, PRIVACY);
  return new Response("Not found.", { status: 404 });
}

async function authorize(request: Request, env: Env, url: URL): Promise<Response> {
  const auth = await env.OAUTH_PROVIDER.parseAuthRequest(request);
  // Only local apps such as Claude Code may sign in: a client registered with a web redirect
  // could otherwise send someone's Drive grant to its own server.
  const redirectUri = new URL(auth.redirectUri);
  if (redirectUri.protocol !== "http:" || !LOOPBACK.has(redirectUri.hostname)) {
    return page(400, "<p>This server only signs in local apps such as Claude Code.</p>");
  }
  const { verifier, challenge } = await pkce();
  const { state, headers } = await env.OAUTH_PROVIDER.beginUpstream(auth, { data: { verifier } });
  headers.set("location", consentUrl(env, `${url.origin}/callback`, state, challenge));
  return new Response(null, { status: 302, headers });
}

async function callback(request: Request, env: Env, url: URL): Promise<Response> {
  // Checks `state` against this browser's binding cookie; works once.
  const { request: auth, data, headers } = await env.OAUTH_PROVIDER.finishUpstream<{ verifier: string }>(request);
  const deny = (description: string) => {
    headers.set("location", authorizationErrorRedirect(auth, "access_denied", description));
    return new Response(null, { status: 302, headers });
  };
  const code = url.searchParams.get("code");
  if (!code) return deny(`Google sign-in was not completed (${url.searchParams.get("error") ?? "no code"}).`);
  const tokens = await exchangeCode(env, code, `${url.origin}/callback`, data.verifier);
  if (!tokens.scope?.split(" ").includes(DRIVE_SCOPE)) {
    return deny("Google Drive read access was not granted. Sign in again and tick the Drive checkbox.");
  }
  if (!tokens.refresh_token) return deny("Google did not return a refresh token. Sign in again.");
  const account = await userEmail(tokens.access_token);
  const props: Props = { email: account.email, googleAccessToken: tokens.access_token, googleRefreshToken: tokens.refresh_token };
  const { redirectTo } = await env.OAUTH_PROVIDER.completeAuthorization({
    request: auth,
    userId: account.sub,
    metadata: { label: account.email },
    scope: auth.scope,
    props: { ...props, googleExpiresIn: tokens.expires_in },
  });
  headers.set("location", redirectTo);
  return new Response(null, { status: 302, headers });
}

/** Keep the Google access token in step with Claude Code's: renew it on each refresh. */
export async function renewGoogle(options: TokenExchangeCallbackOptions<Env>) {
  const props = options.props as Props & { googleExpiresIn?: number };
  if (options.grantType === "authorization_code") {
    const { googleExpiresIn, ...rest } = props;
    return { newProps: rest, accessTokenTTL: Math.max(60, (googleExpiresIn ?? 3600) - EARLY) };
  }
  if (options.grantType !== "refresh_token") return;
  try {
    const t = await refresh(options.env, props.googleRefreshToken);
    const newProps: Props = { ...props, googleAccessToken: t.access_token, googleRefreshToken: t.refresh_token ?? props.googleRefreshToken };
    return { newProps, accessTokenTTL: Math.max(60, t.expires_in - EARLY) };
  } catch (e) {
    if (e instanceof GoogleTokenError && e.invalid) {
      throw new OAuthError("invalid_grant", { description: "Google revoked the sign-in; sign in again from /mcp." });
    }
    throw new OAuthError("temporarily_unavailable", { description: "Could not reach Google to renew the sign-in.", statusCode: 503 });
  }
}

// Google's consent screen needs a homepage and a privacy policy on the app's domain.
const REPO = "https://github.com/q33566/my_plugins";
const HOME = `<h1>Kurt Folder Reader</h1>
<p>A remote MCP server for Claude Code that reads Google Drive folders (read-only), after you sign in
with Google. Source and install steps: <a href="${REPO}">${REPO}</a>.</p>
<p><a href="/privacy">Privacy policy</a></p>`;
const PRIVACY = `<h1>Privacy policy — Kurt Folder Reader</h1>
<p><b>What the app accesses.</b> With your consent, it requests the Google scope
<code>https://www.googleapis.com/auth/drive.readonly</code> (plus your email address, to show which
account is signed in) and uses it only to list and read files inside the Drive folder you ask Claude
about. It never writes, deletes or shares anything.</p>
<p><b>Where your data goes.</b> When you ask, the server fetches a file from Google Drive and returns
it to your Claude Code session in the same request. It does not store file contents or log them.</p>
<p><b>Your Google tokens</b> are kept by this server, encrypted with a key that only the token held by
your Claude Code can unlock, so the server cannot read them unless your Claude Code calls it. They
are used only to call Google Drive for you and to renew the sign-in.</p>
<p><b>Removing access.</b> In Claude Code, run <code>/mcp</code> and clear the sign-in for gdrive-reader,
and remove the app at <a href="https://myaccount.google.com/permissions">myaccount.google.com/permissions</a>;
the stored grant then stops working.</p>
<p><b>Contact.</b> Open an issue at <a href="${REPO}/issues">${REPO}/issues</a>.</p>`;

function page(status: number, body: string): Response {
  return new Response(`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">` +
    `<title>Kurt Folder Reader</title><body style="font-family:system-ui;max-width:42rem;margin:2rem auto;padding:0 1rem;line-height:1.5">${body}`, {
    status, headers: { "content-type": "text/html; charset=utf-8" },
  });
}

function redirect(location: string): Response {
  return new Response(null, { status: 302, headers: { location } });
}

function escape(s: string): string {
  return s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}
