"""The MCP server as Claude Code runs it: a stdio subprocess, Google replaced by local fakes."""
import base64
import json
import os
import sys
import time
import urllib.parse
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

from tests.fake_broker import FakeBroker
from tests.fakes import FOLDER, FakeDrive
from tests.live import serve
from tests.pdfs import blank_pdf, text_pdf

ROOT = Path(__file__).resolve().parents[1]
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def make_drive():
    d = FakeDrive(token="at-live")
    d.add("ROOT", "Take-home test folder", FOLDER)
    d.add("pdf", "Spec.pdf", "application/pdf", "ROOT", text_pdf("Hello Drive PDF"))
    d.add("scan", "Scan.pdf", "application/pdf", "ROOT", blank_pdf(1))
    d.add("img", "Photo.png", "image/png", "ROOT", PNG)
    d.add("outside", "Private.txt", "text/plain", None, b"secret")
    return d


@asynccontextmanager
async def session(tmp_path, drive, broker=None, signed_in=True, elicit=None):
    drive_srv, drive_url = serve(drive.handler)
    broker = broker or FakeBroker()
    broker_srv, broker_url = serve(broker.handler)
    token = tmp_path / "token.json"
    if signed_in:
        token.write_text(json.dumps({"access_token": "at-live", "refresh_token": "rt-1",
                                     "expires_at": time.time() + 3600}))
    env = {**os.environ, "GDRIVE_FOLDER": "https://drive.google.com/drive/folders/ROOT?usp=sharing",
           "GDRIVE_BROKER_URL": broker_url, "GDRIVE_CLIENT_ID": "cid", "GDRIVE_TOKEN_PATH": str(token),
           "GDRIVE_API_BASE": drive_url}
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "server.py")], env=env, cwd=str(ROOT))
    try:
        async with stdio_client(params) as (r, w):
            kwargs = {"elicitation_callback": elicit} if elicit else {}
            async with ClientSession(r, w, **kwargs) as s:
                init = await s.initialize()
                s.init_result = init
                yield s
    finally:
        drive_srv.shutdown()
        broker_srv.shutdown()


def text_of(result):
    return "\n".join(c.text for c in result.content if isinstance(c, types.TextContent))


async def test_tools_load_upfront_and_instructions_fit(tmp_path):
    async with session(tmp_path, make_drive()) as s:
        tools = {t.name: t for t in (await s.list_tools()).tools}
        assert set(tools) == {"drive_auth_status", "drive_list_folder", "drive_read_file"}
        for t in tools.values():
            assert t.meta == {"anthropic/alwaysLoad": True}
            assert t.annotations.readOnlyHint is True
        instructions = s.init_result.instructions
        assert len(instructions) <= 2048 and "drive_list_folder" in instructions


async def test_list_then_read_pdf_scan_and_image(tmp_path):
    async with session(tmp_path, make_drive()) as s:
        listing = json.loads(text_of(await s.call_tool("drive_list_folder", {})))
        assert {f["name"] for f in listing["files"]} == {"Spec.pdf", "Scan.pdf", "Photo.png"}
        pdf = await s.call_tool("drive_read_file", {"file": "pdf"})
        assert not pdf.isError and "Hello Drive PDF" in text_of(pdf)
        scan = await s.call_tool("drive_read_file", {"file": "scan"})
        blobs = [c for c in scan.content if isinstance(c, types.EmbeddedResource)]
        assert blobs and blobs[0].resource.mimeType == "application/pdf"
        assert base64.b64decode(blobs[0].resource.blob) == blank_pdf(1)
        assert "Read" in text_of(scan)
        img = await s.call_tool("drive_read_file", {"file": "img"})
        images = [c for c in img.content if isinstance(c, types.ImageContent)]
        assert images and base64.b64decode(images[0].data) == PNG and images[0].mimeType == "image/png"


async def test_reading_outside_the_folder_is_an_error(tmp_path):
    async with session(tmp_path, make_drive()) as s:
        out = await s.call_tool("drive_read_file", {"file": "outside"})
        assert out.isError and "outside the configured folder" in text_of(out)


async def test_not_signed_in_without_url_prompt_returns_the_link_at_once(tmp_path):
    async with session(tmp_path, make_drive(), signed_in=False) as s:
        out = await s.call_tool("drive_list_folder", {})
        text = text_of(out)
        assert "https://accounts.google.com/o/oauth2/v2/auth?" in text
        assert "drive_list_folder" in text or "again" in text


async def test_sign_in_through_url_prompt_then_list(tmp_path):
    broker = FakeBroker()
    seen = {}

    async def elicit(ctx, params):
        seen["url"] = params.url
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(params.url).query))
        broker.codes["code-x"] = q["code_challenge"]
        port = int(q["state"].split(".")[0])
        async with httpx.AsyncClient() as browser:  # the user finishing consent in the browser
            await browser.get(f"http://127.0.0.1:{port}/callback", params={"code": "code-x", "state": q["state"]})
        return types.ElicitResult(action="accept")

    drive = make_drive()
    drive.token = "at-1"
    async with session(tmp_path, drive, broker=broker, signed_in=False, elicit=elicit) as s:
        status = await s.call_tool("drive_auth_status", {})
        assert "Signed in" in text_of(status), text_of(status)
        assert seen["url"].startswith("https://accounts.google.com/")
        listing = json.loads(text_of(await s.call_tool("drive_list_folder", {})))
        assert len(listing["files"]) == 3


async def test_declined_url_prompt_returns_the_link(tmp_path):
    async def decline(ctx, params):
        return types.ElicitResult(action="decline")

    async with session(tmp_path, make_drive(), signed_in=False, elicit=decline) as s:
        out = text_of(await s.call_tool("drive_auth_status", {}))
        assert "https://accounts.google.com/o/oauth2/v2/auth?" in out


def browser_for(broker, code):
    """An elicitation callback that plays the user: accept, then finish consent in the browser."""
    async def elicit(ctx, params):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(params.url).query))
        broker.codes[code] = q["code_challenge"]
        port = int(q["state"].split(".")[0])
        async with httpx.AsyncClient() as browser:
            await browser.get(f"http://127.0.0.1:{port}/callback", params={"code": code, "state": q["state"]})
        return types.ElicitResult(action="accept")
    return elicit


async def test_revoked_sign_in_then_signing_in_again_completes_the_request(tmp_path):
    broker = FakeBroker()
    drive = make_drive()
    drive.token = "at-1"
    (tmp_path / "token.json").write_text(json.dumps({"access_token": "stale", "refresh_token": "revoked", "expires_at": 0}))
    async with session(tmp_path, drive, broker=broker, signed_in=False, elicit=browser_for(broker, "code-r")) as s:
        out = await s.call_tool("drive_list_folder", {})
        assert not out.isError, text_of(out)
        assert len(json.loads(text_of(out))["files"]) == 3


async def test_failed_exchange_reason_reaches_the_tool_result(tmp_path):
    broker = FakeBroker()  # the code below is never registered, so /token answers invalid_grant

    async def elicit(ctx, params):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(params.url).query))
        port = int(q["state"].split(".")[0])
        async with httpx.AsyncClient() as browser:
            await browser.get(f"http://127.0.0.1:{port}/callback", params={"code": "unknown", "state": q["state"]})
        return types.ElicitResult(action="accept")

    async with session(tmp_path, make_drive(), broker=broker, signed_in=False, elicit=elicit) as s:
        out = text_of(await s.call_tool("drive_auth_status", {}))
        assert "Exchanging the sign-in code failed" in out and "invalid_grant" in out


async def test_text_link_failure_reason_is_shown_on_the_next_call(tmp_path):
    broker = FakeBroker()
    async with session(tmp_path, make_drive(), broker=broker, signed_in=False) as s:
        first = text_of(await s.call_tool("drive_list_folder", {}))
        url = first[first.index("https://accounts.google.com"):].split()[0]
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        async with httpx.AsyncClient() as browser:  # consent finished, but the code is bad
            await browser.get(f"http://127.0.0.1:{q['state'].split('.')[0]}/callback",
                              params={"code": "unknown", "state": q["state"]})
        second = text_of(await s.call_tool("drive_list_folder", {}))
        assert "previous sign-in failed" in second.lower() and "invalid_grant" in second


async def test_a_failed_sign_in_is_reported_once_and_tells_claude_to_ask_first(tmp_path):
    broker = FakeBroker()
    calls = []

    async def elicit(ctx, params):
        calls.append(params.url)
        if len(calls) > 1:
            return types.ElicitResult(action="decline")
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(params.url).query))
        async with httpx.AsyncClient() as browser:
            await browser.get(f"http://127.0.0.1:{q['state'].split('.')[0]}/callback",
                              params={"code": "unknown", "state": q["state"]})
        return types.ElicitResult(action="accept")

    async with session(tmp_path, make_drive(), broker=broker, signed_in=False, elicit=elicit) as s:
        first = text_of(await s.call_tool("drive_auth_status", {}))
        assert "ask before starting a new sign-in" in first and ".." not in first
        second = text_of(await s.call_tool("drive_auth_status", {}))
        assert "previous sign-in failed" not in second.lower()


async def test_revoked_sign_in_on_the_text_link_path_says_why(tmp_path):
    (tmp_path / "token.json").write_text(json.dumps({"access_token": "stale", "refresh_token": "revoked", "expires_at": 0}))
    async with session(tmp_path, make_drive(), signed_in=False) as s:
        out = text_of(await s.call_tool("drive_list_folder", {}))
        assert "no longer accepts the saved sign-in" in out and "https://accounts.google.com" in out


async def test_backend_error_during_refresh_is_not_retried_in_full(tmp_path):
    class FlakyBroker(FakeBroker):
        def handler(self, request):
            if request.url.path == "/refresh":
                self.calls.append(("/refresh", {}))
                return httpx.Response(502, text="<html>Bad gateway</html>")
            return super().handler(request)
    broker = FlakyBroker()
    (tmp_path / "token.json").write_text(json.dumps({"access_token": "stale", "refresh_token": "rt-1", "expires_at": 0}))
    async with session(tmp_path, make_drive(), broker=broker, signed_in=False) as s:
        out = await s.call_tool("drive_list_folder", {})
        assert out.isError and "Could not refresh" in text_of(out)
    assert [c for c in broker.calls if c[0] == "/refresh"] == [("/refresh", {})]
