// End to end through the Worker: Claude Code registers, signs in through a fake Google,
// gets a token, calls the MCP tools, and refreshes.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import worker, { type Env } from "../src/index";
import { fakeKV } from "./fake-kv";

const BASE = "https://gdrive.example.workers.dev";
const ROOT = "rootFolder123";
const REDIRECT = "http://localhost:33418/callback";
const DRIVE = "https://www.googleapis.com/auth/drive.readonly";
const ctx = { waitUntil() {}, passThroughOnException() {} } as unknown as ExecutionContext;

let env: Env;
let google: { refreshes: number; grantScope: string; tokens: string[] };

function call(path: string, init?: RequestInit) {
  return worker.fetch(new Request(BASE + path, init), env, ctx);
}

const json = (body: unknown, status = 200) => Response.json(body, { status });

const TREE: Record<string, { id: string; name: string; mimeType: string; size?: string; parents: string[] }> = {
  [ROOT]: { id: ROOT, name: "Backend Exercise", mimeType: "application/vnd.google-apps.folder", parents: ["top"] },
  sub1: { id: "sub1", name: "Sub", mimeType: "application/vnd.google-apps.folder", parents: [ROOT] },
  pdf1: { id: "pdf1", name: "manual.pdf", mimeType: "application/pdf", size: "4", parents: [ROOT] },
  img1: { id: "img1", name: "pic.png", mimeType: "image/png", size: "3", parents: ["sub1"] },
  doc1: { id: "doc1", name: "Notes", mimeType: "application/vnd.google-apps.document", parents: ["sub1"] },
  out1: { id: "out1", name: "secret.txt", mimeType: "text/plain", size: "6", parents: ["elsewhere"] },
};

function fakeGoogle(input: RequestInfo | URL, init?: RequestInit): Response {
  const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url);
  if (url.href === "https://oauth2.googleapis.com/token") {
    const form = new URLSearchParams(String(init?.body));
    expect(form.get("client_secret")).toBe("s3cret");
    if (form.get("grant_type") === "authorization_code") {
      expect(form.get("code")).toBe("google-code");
      expect(form.get("code_verifier")).toBeTruthy();
      return json({ access_token: "g-access-1", refresh_token: "g-refresh", expires_in: 3599, scope: google.grantScope });
    }
    google.refreshes++;
    expect(form.get("refresh_token")).toBe("g-refresh");
    return json({ access_token: `g-access-${google.refreshes + 1}`, expires_in: 3599, scope: google.grantScope });
  }
  if (url.href === "https://openidconnect.googleapis.com/v1/userinfo") return json({ sub: "user-1", email: "reader@example.com" });
  if (url.hostname === "www.googleapis.com") {
    google.tokens.push(new Headers(init?.headers).get("authorization") ?? "");
    if (url.pathname === "/drive/v3/about") return json({ user: { emailAddress: "reader@example.com" } });
    if (url.pathname === "/drive/v3/files") {
      const parent = /'([^']+)' in parents/.exec(url.searchParams.get("q") ?? "")![1];
      return json({ files: Object.values(TREE).filter((f) => f.parents.includes(parent)) });
    }
    const m = /^\/drive\/v3\/files\/([^/]+)(\/export)?$/.exec(url.pathname);
    const f = m && TREE[m[1]];
    if (!f) return json({ error: { message: "File not found", errors: [{ reason: "notFound" }] } }, 404);
    if (m[2]) return new Response("Doc text");
    if (url.searchParams.get("alt") === "media") return new Response(f.id === "img1" ? new Uint8Array([1, 2, 3]) : "%PDF");
    return json(f);
  }
  throw new Error(`unexpected fetch ${url.href}`);
}

beforeEach(() => {
  env = {
    GOOGLE_CLIENT_ID: "cid.apps.googleusercontent.com",
    GOOGLE_CLIENT_SECRET: "s3cret",
    DEFAULT_FOLDER: `https://drive.google.com/drive/folders/${ROOT}`,
    MCP_RESOURCE: `${BASE}/mcp`,
    OAUTH_KV: fakeKV(),
  } as Env;
  google = { refreshes: 0, grantScope: `openid email ${DRIVE}`, tokens: [] };
  vi.stubGlobal("fetch", vi.fn(fakeGoogle));
});
afterEach(() => vi.unstubAllGlobals());

async function pkcePair() {
  const verifier = "v".repeat(43);
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)));
  const challenge = btoa(String.fromCharCode(...digest)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return { verifier, challenge };
}

async function register(redirect = REDIRECT): Promise<string> {
  const res = await call("/register", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ client_name: "Claude Code", redirect_uris: [redirect], token_endpoint_auth_method: "none", grant_types: ["authorization_code", "refresh_token"], response_types: ["code"] }),
  });
  expect(res.status).toBe(201);
  return ((await res.json()) as { client_id: string }).client_id;
}

function authorizeQuery(clientId: string, challenge: string, redirect = REDIRECT) {
  return new URLSearchParams({ response_type: "code", client_id: clientId, redirect_uri: redirect, code_challenge: challenge, code_challenge_method: "S256", state: "client-state", resource: `${BASE}/mcp` });
}

/** Register, consent at the fake Google, and exchange the code: Claude Code's whole sign-in. */
async function signIn() {
  const clientId = await register();
  const { verifier, challenge } = await pkcePair();
  const start = await call(`/authorize?${authorizeQuery(clientId, challenge)}`);
  expect(start.status).toBe(302);
  const consent = new URL(start.headers.get("location")!);
  expect(consent.origin + consent.pathname).toBe("https://accounts.google.com/o/oauth2/v2/auth");
  expect(consent.searchParams.get("scope")).toContain(DRIVE);
  expect(consent.searchParams.get("access_type")).toBe("offline");
  expect(consent.searchParams.get("redirect_uri")).toBe(`${BASE}/callback`);
  const cookie = start.headers.getSetCookie().map((c) => c.split(";")[0]).join("; ");

  const back = await call(`/callback?code=google-code&state=${encodeURIComponent(consent.searchParams.get("state")!)}`, { headers: { cookie } });
  expect(back.status).toBe(302);
  const done = new URL(back.headers.get("location")!);
  expect(done.origin + done.pathname).toBe(REDIRECT);
  expect(done.searchParams.get("state")).toBe("client-state");

  const token = await call("/token", {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ grant_type: "authorization_code", code: done.searchParams.get("code")!, redirect_uri: REDIRECT, client_id: clientId, code_verifier: verifier, resource: `${BASE}/mcp` }),
  });
  expect(token.status).toBe(200);
  const body = (await token.json()) as { access_token: string; refresh_token: string; expires_in: number };
  return { ...body, clientId };
}

async function tool(accessToken: string, name: string, args: Record<string, unknown> = {}) {
  const res = await call("/mcp", {
    method: "POST",
    headers: { host: new URL(BASE).host, authorization: `Bearer ${accessToken}`, "content-type": "application/json", accept: "application/json, text/event-stream", "mcp-protocol-version": "2025-06-18" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name, arguments: args } }),
  });
  const raw = await res.text();
  expect(res.status, raw).toBe(200);
  const payload = raw.startsWith("{") ? raw : raw.split("\n").find((l) => l.startsWith("data: "))!.slice(6);
  return (JSON.parse(payload) as { result: { content: { type: string; text?: string; data?: string; resource?: { blob: string; mimeType: string } }[]; isError?: boolean } }).result;
}

describe("sign-in", () => {
  it("signs Claude Code in through Google and lets the tools use the Google token", async () => {
    const { access_token, expires_in } = await signIn();
    expect(expires_in).toBeLessThanOrEqual(3599 - 300); // expires before Google's, so Claude Code refreshes first
    const status = await tool(access_token, "drive_auth_status");
    expect(status.content[0].text).toContain("Signed in as reader@example.com");
    expect(google.tokens.at(-1)).toBe("Bearer g-access-1");
  });

  it("asks unauthenticated MCP requests to sign in (401 with resource metadata)", async () => {
    const res = await call("/mcp", { method: "POST", body: "{}" });
    expect(res.status).toBe(401);
    expect(res.headers.get("www-authenticate")).toContain("resource_metadata");
  });

  it("refuses clients that redirect anywhere but this computer", async () => {
    const clientId = await register("https://evil.example/cb");
    const { challenge } = await pkcePair();
    const res = await call(`/authorize?${authorizeQuery(clientId, challenge, "https://evil.example/cb")}`);
    expect(res.status).toBe(400);
    expect(res.headers.get("location")).toBeNull();
  });

  it("refuses a Google callback from a browser that did not start the sign-in", async () => {
    const clientId = await register();
    const { challenge } = await pkcePair();
    const start = await call(`/authorize?${authorizeQuery(clientId, challenge)}`);
    const state = new URL(start.headers.get("location")!).searchParams.get("state")!;
    const res = await call(`/callback?code=google-code&state=${encodeURIComponent(state)}`); // no binding cookie
    expect(res.status).toBe(400);
  });

  it("sends the user back with an error when the Drive checkbox was left unticked", async () => {
    google.grantScope = "openid email";
    const clientId = await register();
    const { challenge } = await pkcePair();
    const start = await call(`/authorize?${authorizeQuery(clientId, challenge)}`);
    const cookie = start.headers.getSetCookie().map((c) => c.split(";")[0]).join("; ");
    const state = new URL(start.headers.get("location")!).searchParams.get("state")!;
    const res = await call(`/callback?code=google-code&state=${encodeURIComponent(state)}`, { headers: { cookie } });
    const loc = new URL(res.headers.get("location")!);
    expect(loc.origin + loc.pathname).toBe(REDIRECT);
    expect(loc.searchParams.get("error")).toBe("access_denied");
    expect(loc.searchParams.get("error_description")).toContain("Drive");
  });

  it("renews the Google token when Claude Code refreshes", async () => {
    const { refresh_token, clientId } = await signIn();
    const res = await call("/token", {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ grant_type: "refresh_token", refresh_token, client_id: clientId, resource: `${BASE}/mcp` }),
    });
    expect(res.status).toBe(200);
    const { access_token } = (await res.json()) as { access_token: string };
    expect(google.refreshes).toBe(1);
    await tool(access_token, "drive_auth_status");
    expect(google.tokens.at(-1)).toBe("Bearer g-access-2");
  });

  it("makes Claude Code sign in again when Google has revoked the grant", async () => {
    const { refresh_token, clientId } = await signIn();
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
      String(input) === "https://oauth2.googleapis.com/token" ? json({ error: "invalid_grant" }, 400) : fakeGoogle(input, init)));
    const res = await call("/token", {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ grant_type: "refresh_token", refresh_token, client_id: clientId, resource: `${BASE}/mcp` }),
    });
    expect(res.status).toBe(400);
    expect(((await res.json()) as { error: string }).error).toBe("invalid_grant");
  });
});

describe("tools", () => {
  let token: string;
  beforeEach(async () => {
    token = (await signIn()).access_token;
  });

  it("lists the default folder with subfolders and paths", async () => {
    const r = await tool(token, "drive_list_folder");
    const listing = JSON.parse(r.content[0].text!) as { folder: { name: string }; files: { path: string }[] };
    expect(listing.folder.name).toBe("Backend Exercise");
    expect(listing.files.map((f) => f.path).sort()).toEqual(["Sub/", "Sub/Notes", "Sub/pic.png", "manual.pdf"]);
  });

  it("returns an image as an image", async () => {
    const r = await tool(token, "drive_read_file", { file: "img1" });
    expect(r.content[1]).toEqual({ type: "image", data: "AQID", mimeType: "image/png" });
  });

  it("returns a PDF as an embedded file for Claude Code to save", async () => {
    const r = await tool(token, "drive_read_file", { file: "https://drive.google.com/file/d/pdf1/view" });
    expect(r.content[0].text).toContain("Read");
    expect(r.content[1].resource).toMatchObject({ mimeType: "application/pdf", blob: btoa("%PDF") });
  });

  it("exports a Google Doc as text", async () => {
    const r = await tool(token, "drive_read_file", { file: "doc1" });
    expect(r.content[0].text).toContain("Doc text");
  });

  it("refuses a file outside the folder", async () => {
    const r = await tool(token, "drive_read_file", { file: "out1" });
    expect(r.isError).toBe(true);
    expect(r.content[0].text).toContain("outside the folder");
  });

  it("explains a missing folder", async () => {
    const r = await tool(token, "drive_list_folder", { folder: "nope" });
    expect(r.isError).toBe(true);
    expect(r.content[0].text).toContain("not found");
  });

  it("serves the homepage and privacy policy without sign-in", async () => {
    expect((await call("/")).status).toBe(200);
    expect(await (await call("/privacy")).text()).toContain("drive.readonly");
  });
});
