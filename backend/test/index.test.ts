import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import worker, { type Env } from "../src/index";

const env: Env = { GOOGLE_CLIENT_ID: "cid.apps.googleusercontent.com", GOOGLE_CLIENT_SECRET: "s3cret" };
const BASE = "https://broker.example.workers.dev";
const STATE = "53682.abcdefghijklmnopqrstuvwxyz012345";

function call(path: string, init?: RequestInit, e: Env = env) {
  return worker.fetch(new Request(BASE + path, init), e);
}
function post(path: string, body: unknown) {
  return call(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
}

afterEach(() => vi.unstubAllGlobals());
// Any test that forgets to stub fetch must fail instead of reaching the real Google.
beforeEach(() => vi.stubGlobal("fetch", () => { throw new Error("unstubbed fetch"); }));

describe("/callback relays Google's redirect to the local listener", () => {
  it("redirects code and state to 127.0.0.1 on the port inside state", async () => {
    const res = await call(`/callback?code=4%2F0Ab-x&state=${STATE}&scope=x`);
    expect(res.status).toBe(302);
    expect(res.headers.get("location")).toBe(`http://127.0.0.1:53682/callback?code=4%2F0Ab-x&state=${STATE}`);
  });

  it("passes Google's error through", async () => {
    const res = await call(`/callback?error=access_denied&state=${STATE}`);
    expect(res.headers.get("location")).toBe(`http://127.0.0.1:53682/callback?error=access_denied&state=${STATE}`);
  });

  it("refuses a state that does not carry a valid loopback port", async () => {
    for (const bad of ["evil.com.abcdefghijklmnopqrstuvwxyz", "80.abcdefghijklmnopqrstuvwxyz", "99999.abcdefghijklmnopqrstuvwxyz", "53682.short", ""]) {
      const res = await call(`/callback?code=x&state=${encodeURIComponent(bad)}`);
      expect(res.status, bad).toBe(400);
      expect(res.headers.get("location")).toBeNull();
    }
  });
});

describe("/token and /refresh add the client secret and call Google", () => {
  it("exchanges code + verifier with the secret and the backend's redirect URI", async () => {
    const google = vi.fn(async (_url: string, _init: RequestInit) =>
      Response.json({ access_token: "at", expires_in: 3599, refresh_token: "rt", scope: "s", token_type: "Bearer", id_token: "drop-me" }));
    vi.stubGlobal("fetch", google);
    const res = await post("/token", { code: "4/0Ab", code_verifier: "v".repeat(64) });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ access_token: "at", expires_in: 3599, refresh_token: "rt", scope: "s", token_type: "Bearer" });
    expect(res.headers.get("cache-control")).toBe("no-store");
    const [url, init] = google.mock.calls[0];
    expect(url).toBe("https://oauth2.googleapis.com/token");
    const sent = new URLSearchParams(init.body as string);
    expect(Object.fromEntries(sent)).toEqual({
      grant_type: "authorization_code", code: "4/0Ab", code_verifier: "v".repeat(64),
      client_id: env.GOOGLE_CLIENT_ID, client_secret: "s3cret", redirect_uri: BASE + "/callback",
    });
  });

  it("refreshes with the secret and passes Google's error status through", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      Response.json({ error: "invalid_grant", error_description: "Token has been expired or revoked." }, { status: 400 })));
    const res = await post("/refresh", { refresh_token: "1//rt" });
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "invalid_grant", error_description: "Token has been expired or revoked." });
  });

  it("rejects malformed requests without calling Google", async () => {
    const google = vi.fn();
    vi.stubGlobal("fetch", google);
    expect((await post("/token", { code: "x" })).status).toBe(400);
    expect((await post("/refresh", {})).status).toBe(400);
    expect((await call("/token", { method: "POST", body: "not json" })).status).toBe(400);
    expect((await call("/token")).status).toBe(405);
    expect((await post("/token", { code: "c".repeat(5000), code_verifier: "v".repeat(64) })).status).toBe(400);
    expect(google).not.toHaveBeenCalled();
  });

  it("reports a missing secret as a server configuration error without calling Google", async () => {
    const google = vi.fn();
    vi.stubGlobal("fetch", google);
    const noSecret = await call("/token", { method: "POST", body: JSON.stringify({ code: "c", code_verifier: "v".repeat(64) }) },
      { GOOGLE_CLIENT_ID: "cid", GOOGLE_CLIENT_SECRET: "" });
    expect(noSecret.status).toBe(500);
    expect(google).not.toHaveBeenCalled();
  });

  it("explains a Google error that has no JSON body", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>oops</html>", { status: 500 })));
    const res = await post("/refresh", { refresh_token: "1//rt" });
    expect(res.status).toBe(500);
    expect(await res.json()).toEqual({ error: "google_error", error_description: "Google answered HTTP 500 without details." });
  });
});

describe("other routes", () => {
  it("returns 404 for unknown paths", async () => {
    expect((await call("/nope")).status).toBe(404);
  });
});
