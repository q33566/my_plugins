"""Folder listing and file reading on top of DriveClient."""
import io
import re

from pypdf import PdfReader

from .drive import FOLDER, SHORTCUT, DriveClient, DriveError

EXPORTS = {
    "application/vnd.google-apps.document": ("text/plain", None),
    "application/vnd.google-apps.spreadsheet": ("text/csv", "Exported as CSV: only the first sheet is included."),
    "application/vnd.google-apps.presentation": ("text/plain", None),
}
IMAGES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
TEXT_TYPES = {"application/json", "application/xml", "application/javascript", "application/x-yaml"}


def file_id_from(ref: str) -> str:
    """Accept a bare id or any drive.google.com / docs.google.com URL."""
    ref = ref.strip()
    m = re.search(r"/(?:d|folders)/([A-Za-z0-9_-]+)", ref) or re.search(r"[?&]id=([A-Za-z0-9_-]+)", ref)
    return m.group(1) if m else ref


def _mb(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} MB"


class DriveReader:
    max_items = 2000
    max_depth = 20
    max_download = 50 * 1024 * 1024  # bytes fetched for any one file
    max_image = 10 * 1024 * 1024
    max_text_chars = 200_000

    def __init__(self, client: DriveClient, root_id: str):
        self.client = client
        self.root_id = file_id_from(root_id)
        self._inside: set[str] = set()
        self._root_name = None

    async def list_folder(self) -> dict:
        root = await self.client.get(self.root_id)
        walk = _Walk(self)
        await walk.folder(self.root_id, "", 0)
        self._root_name = root["name"]
        self._inside = {e["id"] for e in walk.out}
        result = {"folder": {"id": root["id"], "name": root["name"]}, "files": walk.out}
        if walk.truncated:
            result["truncated"] = True
        return result

    async def read_file(self, ref: str) -> dict:
        fid = file_id_from(ref)
        if fid not in self._inside:
            await self.list_folder()  # the folder may have changed since the last listing
        if fid not in self._inside:
            raise DriveError(403, f"That file is outside the configured folder \"{self._root_name}\"; "
                                  "only files listed in that folder can be read.")
        meta = await self.client.get(fid)
        info = {"id": meta["id"], "name": meta["name"], "mimeType": meta["mimeType"]}
        mime, size = meta["mimeType"], int(meta.get("size") or 0)
        if mime == FOLDER:
            return {"kind": "unsupported", "file": info,
                    "message": f"\"{meta['name']}\" is a folder; list the folder to see its files."}
        if mime in EXPORTS:
            export_mime, note = EXPORTS[mime]
            text = (await self.client.export(fid, export_mime)).decode("utf-8", "replace")
            return self._text(info, text, note)
        if mime.startswith("application/vnd.google-apps."):
            return {"kind": "unsupported", "file": info,
                    "message": f"\"{meta['name']}\" is a Google {mime.rsplit('.', 1)[-1]} file, which cannot be read as text."}
        is_text = mime.startswith("text/") or mime in TEXT_TYPES
        if mime not in IMAGES and mime != "application/pdf" and not is_text:
            return {"kind": "unsupported", "file": info,
                    "message": f"\"{meta['name']}\" has type {mime}, which this plugin cannot parse."}
        cap = self.max_image if mime in IMAGES else self.max_download
        if size > cap:
            return {"kind": "too_large", "file": info,
                    "message": f"\"{meta['name']}\" is {_mb(size)}; the limit for this type is {_mb(cap)}."}
        data = await self.client.download(fid)
        if mime in IMAGES:
            return {"kind": "image", "data": data, "mimeType": mime, "file": info}
        if mime == "application/pdf":
            return self._pdf(info, data)
        return self._text(info, data.decode("utf-8", "replace"), None)

    def _text(self, info: dict, text: str, note: str | None) -> dict:
        out = {"kind": "text", "file": info, "text": text}
        if len(text) > self.max_text_chars:
            out["text"] = text[: self.max_text_chars]
            note = (note + " " if note else "") + f"Truncated to the first {self.max_text_chars:,} characters."
        if note:
            out["note"] = note
        return out

    def _pdf(self, info: dict, data: bytes) -> dict:
        try:
            pdf = PdfReader(io.BytesIO(data))
            pages = len(pdf.pages)
            text = "\n\n".join((p.extract_text() or "").strip() for p in pdf.pages).strip()
        except Exception as e:  # damaged or encrypted PDF: let Claude try the file itself
            return {"kind": "pdf_blob", "file": info, "data": data, "pages": None,
                    "note": f"The PDF's text could not be extracted here ({type(e).__name__})."}
        if text:
            return self._text(info, text, f"Text layer of a {pages}-page PDF.")
        return {"kind": "pdf_blob", "file": info, "data": data, "pages": pages,
                "note": f"This {pages}-page PDF has no text layer (it is probably scanned)."}


class _Walk:
    def __init__(self, reader: DriveReader):
        self.reader = reader
        self.out: list[dict] = []
        self.visited = {reader.root_id}
        self.truncated = False

    async def folder(self, folder_id: str, prefix: str, depth: int) -> None:
        if depth >= self.reader.max_depth:
            self.truncated = True
            return
        for f in await self.reader.client.children(folder_id):
            if len(self.out) >= self.reader.max_items:
                self.truncated = True
                return
            entry = {"name": f["name"], "id": f["id"], "mimeType": f["mimeType"],
                     "modifiedTime": f.get("modifiedTime")}
            if f["mimeType"] == SHORTCUT:
                target = f.get("shortcutDetails") or {}
                entry.update(id=target.get("targetId", f["id"]),
                             mimeType=target.get("targetMimeType", f["mimeType"]), shortcut=True)
            elif "size" in f:
                entry["size"] = int(f["size"])
            is_folder = entry["mimeType"] == FOLDER
            entry["path"] = prefix + f["name"] + ("/" if is_folder else "")
            self.out.append(entry)
            if is_folder and entry["id"] not in self.visited:  # a shortcut back is listed, not entered
                self.visited.add(entry["id"])
                await self.folder(entry["id"], entry["path"], depth + 1)
