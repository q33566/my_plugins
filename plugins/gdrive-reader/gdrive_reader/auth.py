"""Google sign-in for a local MCP server, with the client secret kept on a backend.

Flow: a loopback listener on 127.0.0.1 waits for the browser; the Google consent
page redirects to the backend's /callback, which only forwards code and state to
that listener (the port travels inside `state`); the server then sends code + PKCE
verifier to the backend's /token, which adds the client secret and asks Google.
The verifier never leaves this process until that exchange.
"""
import asyncio
import base64
import hashlib
import html
import json
import os
import secrets
import tempfile
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

import httpx

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
SCOPE = "https://www.googleapis.com/auth/drive.readonly"
REFRESH_MARGIN = 60  # seconds before expiry at which a token counts as expired
SIGN_IN_TIMEOUT = 600
ASK_FIRST = "Tell the user what happened and ask before starting a new sign-in."


class NotSignedIn(Exception):
    """No usable Google sign-in; the message says what to do."""


@dataclass
class PendingSignIn:
    url: str
    state: str
    verifier: str
    server: asyncio.base_events.Server
    done: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())
    exchanging: bool = False


class Auth:
    def __init__(self, token_path: Path, client_id: str, broker_url: str, http: httpx.AsyncClient,
                 callback_port: int = 0, clock=time.time):
        self.token_path = Path(token_path)
        self.client_id = client_id
        self.broker_url = broker_url.rstrip("/")
        self.http = http
        self.callback_port = callback_port
        self.clock = clock
        self.pending: PendingSignIn | None = None
        self._last_error: str | None = None  # why the last sign-in failed, until someone reports it
        self._refresh_lock = asyncio.Lock()

    # ---- tokens -------------------------------------------------------------

    def _load(self) -> dict | None:
        try:
            return json.loads(self.token_path.read_text())
        except (FileNotFoundError, ValueError):
            return None

    def _save(self, tokens: dict) -> None:
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.token_path.parent, prefix=".token-")
        try:
            os.chmod(tmp, 0o600)
            with os.fdopen(fd, "w") as fh:
                json.dump(tokens, fh)
            os.replace(tmp, self.token_path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def signed_in(self) -> bool:
        return bool((self._load() or {}).get("refresh_token"))

    def sign_out(self) -> None:
        self.token_path.unlink(missing_ok=True)

    async def access_token(self, force_refresh: bool = False) -> str:
        tokens = self._load()
        if not tokens or not tokens.get("refresh_token"):
            raise NotSignedIn("Not signed in to Google Drive yet. Run drive_auth_status to sign in.")
        if not force_refresh and tokens.get("expires_at", 0) - REFRESH_MARGIN > self.clock():
            return tokens["access_token"]
        async with self._refresh_lock:
            latest = self._load() or tokens  # another session may have refreshed already
            if latest.get("access_token") != tokens.get("access_token") and not force_refresh:
                return latest["access_token"]
            return await self._refresh(latest)

    async def _refresh(self, tokens: dict) -> str:
        resp = await self._broker("/refresh", {"refresh_token": tokens["refresh_token"]})
        body = _json(resp)
        if resp.status_code == 400 and body.get("error") == "invalid_grant":
            self.sign_out()
            raise NotSignedIn("Google no longer accepts the saved sign-in (it was revoked or expired). "
                              "Run drive_auth_status to sign in again.")
        if resp.status_code != 200 or "access_token" not in body:
            raise NotSignedIn(f"Could not refresh the Google sign-in: {_describe(resp, body)}. Try again shortly.")
        self._save(self._merge(tokens, body))
        return body["access_token"]

    def _merge(self, old: dict, body: dict) -> dict:
        return {"access_token": body["access_token"],
                "refresh_token": body.get("refresh_token") or old.get("refresh_token"),  # Google omits it on refresh
                "expires_at": self.clock() + int(body.get("expires_in", 3600)),
                "scope": body.get("scope", old.get("scope"))}

    async def _broker(self, path: str, payload: dict) -> httpx.Response:
        try:
            return await self.http.post(self.broker_url + path, json=payload, timeout=30)
        except httpx.TransportError as e:
            raise NotSignedIn(f"Could not reach the sign-in backend at {self.broker_url} "
                              f"({type(e).__name__}). Check the network and try again.")

    # ---- sign-in ------------------------------------------------------------

    async def start_sign_in(self) -> PendingSignIn:
        """Start one sign-in, or return the one already waiting for the browser."""
        if self.pending and not self.pending.done.done():
            return self.pending
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        try:
            server = await asyncio.start_server(self._handle, "127.0.0.1", self.callback_port)
        except OSError as e:
            raise NotSignedIn(f"Could not listen on 127.0.0.1:{self.callback_port} for the sign-in "
                              f"redirect ({e.strerror}). Free that port or choose another callback_port.")
        port = server.sockets[0].getsockname()[1]
        state = f"{port}.{secrets.token_urlsafe(24)}"
        url = GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode({
            "client_id": self.client_id, "redirect_uri": self.broker_url + "/callback",
            "response_type": "code", "scope": SCOPE, "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256",
            "access_type": "offline", "prompt": "consent"})
        self.pending = PendingSignIn(url=url, state=state, verifier=verifier, server=server)
        asyncio.get_running_loop().call_later(SIGN_IN_TIMEOUT, self._expire, self.pending)
        return self.pending

    def note_error(self, message: str) -> None:
        self._last_error = message

    def take_last_error(self) -> str | None:
        error, self._last_error = self._last_error, None
        return error

    def cancel_sign_in(self) -> None:
        if self.pending:
            self._finish(self.pending, NotSignedIn("Sign-in was cancelled."))

    def _expire(self, pending: PendingSignIn) -> None:
        self._finish(pending, NotSignedIn("The sign-in link expired after 10 minutes. "
                                          "Run drive_auth_status to get a new one."))

    def _finish(self, pending: PendingSignIn, error: Exception | None) -> None:
        if not pending.done.done():
            if error:
                pending.done.set_exception(error)
                pending.done.exception()  # mark retrieved: the reason is reported via take_last_error
                self._last_error = f"The previous sign-in failed: {error}"
            else:
                pending.done.set_result(True)
        pending.server.close()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = (await asyncio.wait_for(reader.readline(), 10)).decode("latin-1")
            while (await asyncio.wait_for(reader.readline(), 10)) not in (b"\r\n", b"\n", b""):
                pass
            status, message = await self._callback(request_line)
        except Exception as e:  # never let a stray connection take the server down
            status, message = 400, f"Bad request ({type(e).__name__})."
        body = (f"<!doctype html><meta charset=utf-8><title>Google Drive Reader</title>"
                f"<p>{html.escape(message)}</p>").encode()
        writer.write(f"HTTP/1.1 {status} {'OK' if status == 200 else 'Bad Request'}\r\n"
                     f"Content-Type: text/html; charset=utf-8\r\nContent-Length: {len(body)}\r\n"
                     f"Connection: close\r\n\r\n".encode() + body)
        await writer.drain()
        writer.close()

    async def _callback(self, request_line: str) -> tuple[int, str]:
        pending = self.pending
        parts = request_line.split()
        if len(parts) < 2 or not parts[1].startswith("/callback"):
            return 400, "Not a sign-in callback."
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(parts[1]).query))
        if not pending or pending.done.done() or not secrets.compare_digest(q.get("state", ""), pending.state):
            return 400, "This sign-in link is not the one this plugin is waiting for. Start sign-in again from Claude Code."
        if pending.exchanging:
            return 400, "This sign-in is already being completed. You can close this tab."
        if "error" in q:
            self._finish(pending, NotSignedIn(f"Google sign-in did not complete: {q['error']}. {ASK_FIRST}"))
            return 400, f"Sign-in did not complete ({q['error']}). You can close this tab."
        pending.exchanging = True  # a code is single-use; a second browser hit must not spend it again
        try:
            resp = await self._broker("/token", {"code": q.get("code", ""), "code_verifier": pending.verifier})
            body = _json(resp)
            if resp.status_code != 200 or "access_token" not in body or "refresh_token" not in body:
                raise NotSignedIn(f"Exchanging the sign-in code failed: {_describe(resp, body)}. {ASK_FIRST}")
            try:
                self._save(self._merge({}, body))
            except OSError as e:
                raise NotSignedIn(f"Signed in, but the token could not be saved to {self.token_path} "
                                  f"({e.strerror or type(e).__name__}). Check that folder is writable.")
        except Exception as e:
            if not isinstance(e, NotSignedIn):
                e = NotSignedIn(f"Sign-in failed unexpectedly ({type(e).__name__}: {e}). {ASK_FIRST}")
            self._finish(pending, e)
            return 400, f"Sign-in failed: {e} You can close this tab."
        self._last_error = None
        self._finish(pending, None)
        return 200, "Signed in to Google Drive Reader. You can close this tab and return to Claude Code."


def _json(resp: httpx.Response) -> dict:
    try:
        body = resp.json()
        return body if isinstance(body, dict) else {}
    except ValueError:
        return {}


def _describe(resp: httpx.Response, body: dict) -> str:
    code, detail = body.get("error"), body.get("error_description")
    err = f"{code}: {detail}" if code and detail else code or detail or resp.text[:200]
    return f"HTTP {resp.status_code} {err}".rstrip(". ")
