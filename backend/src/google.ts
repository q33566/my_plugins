/** The Worker as an OAuth client of Google: consent URL, code exchange and refresh. */

export const DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly";
const AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth";
const TOKEN_URL = "https://oauth2.googleapis.com/token";
const USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo";

export interface GoogleEnv {
  GOOGLE_CLIENT_ID: string;
  GOOGLE_CLIENT_SECRET: string;
}

export interface GoogleTokens {
  access_token: string;
  expires_in: number;
  refresh_token?: string;
  scope?: string;
}

/** Thrown when Google refuses a token request; `invalid` means the grant is gone for good. */
export class GoogleTokenError extends Error {
  constructor(readonly invalid: boolean, message: string) {
    super(message);
  }
}

export function consentUrl(env: GoogleEnv, redirectUri: string, state: string, codeChallenge: string): string {
  const params = new URLSearchParams({
    client_id: env.GOOGLE_CLIENT_ID,
    redirect_uri: redirectUri,
    response_type: "code",
    scope: `openid email ${DRIVE_SCOPE}`,
    access_type: "offline", // a refresh token, so the sign-in outlives the one-hour access token
    prompt: "consent", // Google sends the refresh token only on a fresh consent
    include_granted_scopes: "true",
    state,
    code_challenge: codeChallenge,
    code_challenge_method: "S256",
  });
  return `${AUTH_URL}?${params}`;
}

export async function exchangeCode(env: GoogleEnv, code: string, redirectUri: string, verifier: string): Promise<GoogleTokens> {
  return tokenRequest(env, { grant_type: "authorization_code", code, redirect_uri: redirectUri, code_verifier: verifier });
}

export async function refresh(env: GoogleEnv, refreshToken: string): Promise<GoogleTokens> {
  return tokenRequest(env, { grant_type: "refresh_token", refresh_token: refreshToken });
}

export async function userEmail(accessToken: string): Promise<{ sub: string; email: string }> {
  const resp = await fetch(USERINFO_URL, { headers: { authorization: `Bearer ${accessToken}` } });
  if (!resp.ok) throw new GoogleTokenError(false, `Google did not return the account (HTTP ${resp.status}).`);
  const body = (await resp.json()) as { sub?: string; email?: string };
  if (!body.sub) throw new GoogleTokenError(false, "Google did not return the account id.");
  return { sub: body.sub, email: body.email ?? "unknown account" };
}

async function tokenRequest(env: GoogleEnv, fields: Record<string, string>): Promise<GoogleTokens> {
  const resp = await fetch(TOKEN_URL, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ ...fields, client_id: env.GOOGLE_CLIENT_ID, client_secret: env.GOOGLE_CLIENT_SECRET }),
  });
  const body = (await resp.json().catch(() => ({}))) as Partial<GoogleTokens> & { error?: string; error_description?: string };
  if (!resp.ok || !body.access_token) {
    const invalid = body.error === "invalid_grant";
    throw new GoogleTokenError(invalid, `Google refused the token request: ${body.error_description ?? body.error ?? `HTTP ${resp.status}`}.`);
  }
  return body as GoogleTokens;
}

/** A PKCE pair (RFC 7636, S256). */
export async function pkce(): Promise<{ verifier: string; challenge: string }> {
  const verifier = base64url(crypto.getRandomValues(new Uint8Array(32)));
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return { verifier, challenge: base64url(new Uint8Array(digest)) };
}

function base64url(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
