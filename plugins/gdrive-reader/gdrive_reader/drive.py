"""Thin async client for the parts of the Google Drive v3 API the plugin reads."""
import asyncio
import random
from typing import Awaitable, Callable

import httpx

FOLDER = "application/vnd.google-apps.folder"
SHORTCUT = "application/vnd.google-apps.shortcut"
FILE_FIELDS = "id,name,mimeType,size,modifiedTime,parents,shortcutDetails(targetId,targetMimeType)"


class DriveError(Exception):
    """A Drive request failed; `message` is safe to show to the user."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


RETRY_STATUSES = {429, 500, 502, 503, 504}
RATE_LIMIT_REASONS = {"rateLimitExceeded", "userRateLimitExceeded"}  # Drive also sends these as 403


class DriveClient:
    """`access_token(force_refresh=False)` returns a Google access token; on a 401
    the client asks once for a fresh one before giving up."""

    max_attempts = 5

    def __init__(self, http: httpx.AsyncClient, access_token: Callable[..., Awaitable[str]],
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.http = http
        self.access_token = access_token
        self.sleep = sleep

    async def _get(self, path: str, params: dict) -> httpx.Response:
        refreshed = False
        token = await self.access_token()
        for attempt in range(self.max_attempts):
            try:
                resp = await self.http.get(path, params=params, headers={"Authorization": f"Bearer {token}"})
            except httpx.TransportError as e:
                if attempt == self.max_attempts - 1:
                    raise DriveError(0, f"Could not reach Google Drive ({type(e).__name__}); check the network and try again.")
                await self.sleep(_backoff(attempt))
                continue
            if resp.status_code == 401 and not refreshed:
                refreshed = True
                token = await self.access_token(force_refresh=True)
                continue
            retryable = resp.status_code in RETRY_STATUSES or (
                resp.status_code == 403 and _reason(resp) in RATE_LIMIT_REASONS)
            if retryable and attempt < self.max_attempts - 1:
                await self.sleep(_backoff(attempt))
                continue
            if resp.status_code >= 400:
                raise DriveError(resp.status_code, _explain(resp))
            return resp
        raise DriveError(resp.status_code, _explain(resp))

    async def get(self, file_id: str) -> dict:
        resp = await self._get(f"/drive/v3/files/{file_id}",
                               {"fields": FILE_FIELDS, "supportsAllDrives": "true"})
        return resp.json()

    async def user_email(self) -> str:
        resp = await self._get("/drive/v3/about", {"fields": "user(emailAddress)"})
        return resp.json().get("user", {}).get("emailAddress", "unknown account")

    async def download(self, file_id: str) -> bytes:
        resp = await self._get(f"/drive/v3/files/{file_id}", {"alt": "media", "supportsAllDrives": "true"})
        return resp.content

    async def export(self, file_id: str, mime: str) -> bytes:
        resp = await self._get(f"/drive/v3/files/{file_id}/export", {"mimeType": mime})
        return resp.content

    async def children(self, folder_id: str) -> list[dict]:
        out, token = [], None
        while True:
            params = {"q": f"'{folder_id}' in parents and trashed = false",
                      "fields": f"nextPageToken,files({FILE_FIELDS})", "pageSize": "1000",
                      "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
            if token:
                params["pageToken"] = token
            body = (await self._get("/drive/v3/files", params)).json()
            out.extend(body.get("files", []))
            token = body.get("nextPageToken")
            if not token:
                return out


def _backoff(attempt: int) -> float:
    return min(8.0, 0.5 * 2 ** attempt) * (0.5 + random.random() / 2)


def _explain(resp: httpx.Response) -> str:
    detail = _error_message(resp)
    s = resp.status_code
    if s == 401:
        return f"Google rejected the stored sign-in ({detail}). Run drive_auth_status to sign in again."
    if s == 403 and _reason(resp) == "exportSizeLimitExceeded":
        return ("This Google file is too large to export as text (Google's export limit is 10 MB). "
                "Open it in Google Drive instead.")
    if s == 403 and _reason(resp) in RATE_LIMIT_REASONS:
        return f"Google Drive is rate-limiting requests ({detail}); try again in a minute."
    if s == 403:
        return (f"Google Drive refused access ({detail}). Check that this Google account can open the "
                "folder, and that the plugin was granted Drive read access when signing in.")
    if s == 404:
        return (f"File or folder not found ({detail}). It may have been deleted, or this Google "
                "account has no access to it.")
    if s in RETRY_STATUSES:
        return f"Google Drive is busy or unavailable (HTTP {s}: {detail}); try again in a minute."
    return f"Google Drive request failed (HTTP {s}: {detail})."


def _reason(resp: httpx.Response) -> str | None:
    try:
        return resp.json()["error"]["errors"][0]["reason"]
    except Exception:
        return None


def _error_message(resp: httpx.Response) -> str:
    try:
        return resp.json()["error"]["message"]
    except Exception:
        return resp.text[:200] or f"HTTP {resp.status_code}"
