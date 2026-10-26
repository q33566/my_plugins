"""A fake Google Drive v3 API, served through httpx.MockTransport.

Only the parts the plugin uses: files.list with "'<id>' in parents",
files.get (metadata, alt=media) and files.export.
"""
import json
import re
import urllib.parse

import httpx

DEFAULT_FIELDS = ["kind", "id", "name", "mimeType"]  # what Drive v3 returns when `fields` is absent

FOLDER = "application/vnd.google-apps.folder"
SHORTCUT = "application/vnd.google-apps.shortcut"


class FakeDrive:
    def __init__(self, token="good-token"):
        self.files = {}  # id -> metadata dict (+ "_content": bytes, "_exports": {mime: bytes})
        self.token = token
        self.page_size_cap = 1000
        self.requests = []
        self.fail_next = []  # list of status codes to return before serving normally

    def add(self, id, name, mime, parent=None, content=b"", exports=None, **extra):
        meta = {"id": id, "name": name, "mimeType": mime, "parents": [parent] if parent else [],
                "trashed": False, "_content": content, "_exports": exports or {}}
        if mime not in (FOLDER, SHORTCUT) and not mime.startswith("application/vnd.google-apps."):
            meta["size"] = str(len(content))
        meta["modifiedTime"] = "2026-10-01T00:00:00.000Z"
        meta.update(extra)
        self.files[id] = meta
        return meta

    def public(self, meta, fields=None):
        """Only the requested fields, like the real API (`fields` selects; default is minimal)."""
        wanted = _field_names(fields) if fields else DEFAULT_FIELDS
        return {k: v for k, v in meta.items() if not k.startswith("_") and k in wanted}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_next:
            failure = self.fail_next.pop(0)
            status, reason = failure if isinstance(failure, tuple) else (failure, "backendError")
            return httpx.Response(status, json={"error": {"code": status, "message": "injected",
                                                          "errors": [{"reason": reason}]}})
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"error": {"code": 401, "message": "Invalid Credentials"}})
        path = request.url.path
        params = dict(request.url.params)
        if path == "/drive/v3/files":
            return self._list(params)
        if path == "/drive/v3/about":
            return httpx.Response(200, json={"user": {"emailAddress": "grader@example.com"}})
        m = re.fullmatch(r"/drive/v3/files/([^/]+)(/export)?", path)
        if not m:
            return httpx.Response(404, json={"error": {"message": "no route"}})
        fid = urllib.parse.unquote(m.group(1))
        meta = self.files.get(fid)
        if meta is None or meta["trashed"]:
            return httpx.Response(404, json={"error": {"code": 404, "message": f"File not found: {fid}."}})
        if m.group(2):
            mime = params.get("mimeType")
            if mime not in meta["_exports"]:
                return httpx.Response(400, json={"error": {"message": "Export only supports Docs Editors files."}})
            return httpx.Response(200, content=meta["_exports"][mime])
        if params.get("alt") == "media":
            if meta["mimeType"].startswith("application/vnd.google-apps."):
                return httpx.Response(403, json={"error": {"message": "Only files with binary content can be downloaded."}})
            return httpx.Response(200, content=meta["_content"])
        return httpx.Response(200, json=self.public(meta, params.get("fields")))

    def _list(self, params):
        q = params.get("q", "")
        m = re.search(r"'([^']+)' in parents", q)
        parent = m.group(1) if m else None
        m2 = re.fullmatch(r"nextPageToken,files\((.*)\)", params.get("fields", ""))
        items = [self.public(f, m2.group(1) if m2 else None) for f in self.files.values()
                 if parent in f["parents"] and not ("trashed = false" in q and f["trashed"])]
        items.sort(key=lambda f: f["name"])
        size = min(int(params.get("pageSize", 100)), self.page_size_cap)
        start = int(params.get("pageToken", "0") or 0)
        page = items[start:start + size]
        body = {"files": page}
        if start + size < len(items):
            body["nextPageToken"] = str(start + size)
        return httpx.Response(200, json=body)

    def transport(self):
        return httpx.MockTransport(self.handler)


def _field_names(fields: str) -> list[str]:
    """Top-level names in a Drive `fields` selector: "a,b,c(d,e)" -> [a, b, c]."""
    names, depth, cur = [], 0, ""
    for ch in fields:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            names.append(cur.strip())
            cur = ""
            continue
        if depth == 0 and ch not in "()":
            cur += ch
    if cur.strip():
        names.append(cur.strip())
    return names
