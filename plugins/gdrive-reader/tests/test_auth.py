import asyncio
import json
import os
import stat
import time
import urllib.parse

import httpx
import pytest

from gdrive_reader.auth import Auth, NotSignedIn
from tests.fake_broker import FakeBroker

BROKER = "https://broker.example.workers.dev"


def make_auth(tmp_path, broker, port=0, clock=time.time):
    http = httpx.AsyncClient(transport=broker.transport(), base_url=BROKER)
    return Auth(token_path=tmp_path / "token.json", client_id="cid.apps.googleusercontent.com",
                broker_url=BROKER, http=http, callback_port=port, clock=clock)


async def browser_returns(url, broker, code="code-1", state=None):
    """Play Google + the Worker relay: consent, then land on the loopback listener."""
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
    broker.codes[code] = q["code_challenge"]
    port = int(q["state"].split(".")[0])
    async with httpx.AsyncClient() as browser:
        return await browser.get(f"http://127.0.0.1:{port}/callback",
                                 params={"code": code, "state": state or q["state"]})


async def test_sign_in_end_to_end_saves_tokens(tmp_path):
    broker = FakeBroker()
    auth = make_auth(tmp_path, broker)
    pending = await auth.start_sign_in()
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(pending.url).query))
    assert pending.url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert q["client_id"] == "cid.apps.googleusercontent.com"
    assert q["redirect_uri"] == BROKER + "/callback"
    assert q["scope"] == "https://www.googleapis.com/auth/drive.readonly"
    assert q["code_challenge_method"] == "S256" and q["access_type"] == "offline"
    assert q["prompt"] == "consent"  # without it Google sends no refresh token on a later sign-in
    page = await browser_returns(pending.url, broker)
    assert page.status_code == 200 and "signed in" in page.text.lower()
    await asyncio.wait_for(pending.done, 5)
    assert await auth.access_token() == "at-1"
    saved = json.loads((tmp_path / "token.json").read_text())
    assert saved["refresh_token"] == "rt-1"
    if os.name == "posix":
        assert stat.S_IMODE((tmp_path / "token.json").stat().st_mode) == 0o600


async def test_wrong_state_is_rejected_and_sign_in_keeps_waiting(tmp_path):
    broker = FakeBroker()
    auth = make_auth(tmp_path, broker)
    pending = await auth.start_sign_in()
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(pending.url).query))
    forged = q["state"].split(".")[0] + ".forged"
    page = await browser_returns(pending.url, broker, state=forged)
    assert page.status_code == 400 and not pending.done.done()
    await browser_returns(pending.url, broker, code="code-2")
    await asyncio.wait_for(pending.done, 5)
    assert await auth.access_token() == "at-1"


async def test_google_denial_ends_sign_in_with_a_message(tmp_path):
    broker = FakeBroker()
    auth = make_auth(tmp_path, broker)
    pending = await auth.start_sign_in()
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(pending.url).query))
    port = int(q["state"].split(".")[0])
    async with httpx.AsyncClient() as browser:
        page = await browser.get(f"http://127.0.0.1:{port}/callback",
                                 params={"error": "access_denied", "state": q["state"]})
    assert page.status_code == 400
    with pytest.raises(NotSignedIn) as e:
        await asyncio.wait_for(pending.done, 5)
    assert "access_denied" in str(e.value)


async def test_second_sign_in_while_pending_reuses_the_first(tmp_path):
    auth = make_auth(tmp_path, FakeBroker())
    first = await auth.start_sign_in()
    second = await auth.start_sign_in()
    assert second is first
    auth.cancel_sign_in()


async def test_no_tokens_means_not_signed_in(tmp_path):
    with pytest.raises(NotSignedIn):
        await make_auth(tmp_path, FakeBroker()).access_token()


async def test_expired_token_refreshes_and_keeps_the_refresh_token(tmp_path):
    now = [1_000_000.0]
    broker = FakeBroker()
    auth = make_auth(tmp_path, broker, clock=lambda: now[0])
    (tmp_path / "token.json").write_text(json.dumps(
        {"access_token": "old", "refresh_token": "rt-1", "expires_at": now[0] + 30}))
    assert await auth.access_token() == "at-1"  # within the 60 s safety margin → refreshed
    saved = json.loads((tmp_path / "token.json").read_text())
    assert saved["refresh_token"] == "rt-1" and saved["access_token"] == "at-1"
    assert await auth.access_token(force_refresh=True) == "at-2"


async def test_revoked_refresh_token_clears_tokens_and_asks_to_sign_in(tmp_path):
    broker = FakeBroker()
    auth = make_auth(tmp_path, broker)
    (tmp_path / "token.json").write_text(json.dumps(
        {"access_token": "old", "refresh_token": "revoked", "expires_at": 0}))
    with pytest.raises(NotSignedIn) as e:
        await auth.access_token()
    assert "sign in again" in str(e.value)
    assert not (tmp_path / "token.json").exists()


class DownBroker:
    def transport(self):
        def fail(request):
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.MockTransport(fail)


async def test_backend_unreachable_during_exchange_ends_sign_in_with_the_reason(tmp_path):
    auth = make_auth(tmp_path, DownBroker())
    pending = await auth.start_sign_in()
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(pending.url).query))
    port = int(q["state"].split(".")[0])
    async with httpx.AsyncClient() as browser:
        page = await browser.get(f"http://127.0.0.1:{port}/callback", params={"code": "c", "state": q["state"]})
    assert page.status_code == 400 and "sign-in backend" in page.text
    with pytest.raises(NotSignedIn) as e:
        await asyncio.wait_for(pending.done, 5)
    assert "Could not reach the sign-in backend" in str(e.value)


async def test_token_file_that_cannot_be_saved_ends_sign_in_with_the_reason(tmp_path):
    broker = FakeBroker()
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    http = httpx.AsyncClient(transport=broker.transport(), base_url=BROKER)
    auth = Auth(token_path=blocker / "token.json", client_id="cid", broker_url=BROKER, http=http)
    pending = await auth.start_sign_in()
    page = await browser_returns(pending.url, broker)
    assert page.status_code == 400
    with pytest.raises(NotSignedIn) as e:
        await asyncio.wait_for(pending.done, 5)
    assert "could not be saved" in str(e.value)


class SlowBroker(FakeBroker):
    """Google takes a moment to answer, so a second browser hit can arrive meanwhile."""
    def transport(self):
        async def slow(request):
            await asyncio.sleep(0.3)
            return self.handler(request)
        return httpx.MockTransport(slow)


async def test_a_second_callback_for_the_same_sign_in_does_not_reuse_the_code(tmp_path):
    broker = SlowBroker()
    auth = make_auth(tmp_path, broker)
    pending = await auth.start_sign_in()
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(pending.url).query))
    broker.codes["code-1"] = q["code_challenge"]
    port = int(q["state"].split(".")[0])
    async with httpx.AsyncClient() as browser:
        hit = lambda: browser.get(f"http://127.0.0.1:{port}/callback", params={"code": "code-1", "state": q["state"]})
        pages = await asyncio.gather(hit(), hit(), return_exceptions=True)
    token_calls = [c for c in broker.calls if c[0] == "/token"]
    assert len(token_calls) == 1
    await asyncio.wait_for(pending.done, 5)


async def test_listener_accepts_only_loopback_connections(tmp_path):
    auth = make_auth(tmp_path, FakeBroker())
    pending = await auth.start_sign_in()
    assert {s.getsockname()[0] for s in pending.server.sockets} == {"127.0.0.1"}
    auth.cancel_sign_in()


async def test_sign_in_link_expires_and_closes_the_listener(tmp_path, monkeypatch):
    import gdrive_reader.auth as auth_module
    monkeypatch.setattr(auth_module, "SIGN_IN_TIMEOUT", 0.2)
    auth = make_auth(tmp_path, FakeBroker())
    pending = await auth.start_sign_in()
    with pytest.raises(NotSignedIn) as e:
        await asyncio.wait_for(pending.done, 5)
    assert "expired" in str(e.value)
    await asyncio.sleep(0.05)
    assert not pending.server.is_serving()


async def test_exchange_without_a_refresh_token_is_not_a_sign_in(tmp_path):
    class NoRefreshBroker(FakeBroker):
        def handler(self, request):
            resp = super().handler(request)
            body = json.loads(resp.content)
            body.pop("refresh_token", None)
            return httpx.Response(resp.status_code, json=body)
    broker = NoRefreshBroker()
    auth = make_auth(tmp_path, broker)
    pending = await auth.start_sign_in()
    await browser_returns(pending.url, broker)
    with pytest.raises(NotSignedIn):
        await asyncio.wait_for(pending.done, 5)
    assert not auth.signed_in()


async def test_unexpected_error_during_exchange_ends_the_sign_in(tmp_path):
    class OddBroker(FakeBroker):
        def handler(self, request):
            resp = super().handler(request)
            body = json.loads(resp.content)
            body["expires_in"] = "soon"
            return httpx.Response(resp.status_code, json=body)
    broker = OddBroker()
    auth = make_auth(tmp_path, broker)
    pending = await auth.start_sign_in()
    page = await browser_returns(pending.url, broker)
    assert page.status_code == 400
    with pytest.raises(NotSignedIn):
        await asyncio.wait_for(pending.done, 5)
    assert (await auth.start_sign_in()) is not pending
