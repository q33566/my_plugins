"""MCP server: read-only tools over one Google Drive folder."""
import asyncio
import base64
import json

import httpx
from mcp import types
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from .auth import Auth, NotSignedIn
from .config import Config
from .drive import DriveClient, DriveError
from .reader import DriveReader, file_id_from

INSTRUCTIONS = """\
Read-only access to one Google Drive folder (and its subfolders), configured in the \
plugin settings. Use these tools for any request about "the Drive folder", "the folder", \
or the files in it; no URL is needed.

- drive_list_folder: every file in the folder tree with path, id, type and size.
- drive_read_file: one file's content by id or Drive URL. Docs, Sheets (first sheet), \
Slides and text files come back as text; images as images; a PDF as its text, or, when \
it has no text layer, as a file Claude Code saves and you then open with Read.
- drive_auth_status: who is signed in; starts Google sign-in when needed.

Sign-in: the first call opens a Google sign-in link (Claude Code asks before opening it). \
If the link comes back as text instead, ask the user to open it, then call the tool again.

Only files inside the configured folder can be read. There are no write, search or \
watch tools.
"""

READ_ONLY = types.ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)
ALWAYS_LOAD = {"anthropic/alwaysLoad": True}
URL_PROMPT_WAIT = 300  # seconds a tool call waits for the browser after the user accepts the prompt


def build(config: Config) -> FastMCP:
    mcp = FastMCP("gdrive-reader", instructions=INSTRUCTIONS)
    auth = Auth(token_path=config.token_path, client_id=config.client_id, broker_url=config.broker_url,
                http=httpx.AsyncClient(timeout=30), callback_port=config.callback_port)
    client = DriveClient(httpx.AsyncClient(base_url=config.api_base, timeout=60), auth.access_token)
    reader = DriveReader(client, config.folder)

    async def sign_in(ctx: Context, retry_hint: str) -> str | None:
        """None when signed in (possibly just now); otherwise the text to return."""
        if auth.signed_in():
            auth.take_last_error()  # signed in (maybe by another session): an old failure is moot
            return None
        previous = auth.take_last_error()
        try:
            pending = await auth.start_sign_in()
        except NotSignedIn as e:
            return str(e)
        link = (f"Google Drive sign-in is needed. Open this link in a browser, approve read-only "
                f"access, then {retry_hint}:\n{pending.url}")
        if previous:
            link = f"{previous}\n\n{link}"
        caps = ctx.session.client_params.capabilities if ctx.session.client_params else None
        if not (caps and caps.elicitation and caps.elicitation.url):
            return link
        try:
            answer = await ctx.session.elicit_url(
                message="Sign in to Google to give Google Drive Reader read-only access to your Drive folder.",
                url=pending.url, elicitation_id=pending.state.split(".", 1)[1][:16],
                related_request_id=ctx.request_id)
        except Exception:
            return link
        if answer.action != "accept":
            return link
        try:
            await asyncio.wait_for(asyncio.shield(pending.done), URL_PROMPT_WAIT)
        except asyncio.TimeoutError:
            return link
        except NotSignedIn as e:
            auth.take_last_error()  # reported here; do not repeat it on the next call
            return str(e)
        return None

    async def call(ctx: Context, retry_hint: str, work):
        for attempt in range(2):  # a second pass after the stored sign-in was rejected and renewed
            message = await sign_in(ctx, retry_hint)
            if message:
                return message
            try:
                return await work()
            except NotSignedIn as e:
                if attempt or auth.signed_in():  # tokens kept: a transient failure, not a revoked sign-in
                    raise ToolError(str(e))
                auth.note_error(str(e))  # shown with the new sign-in link
            except DriveError as e:
                raise ToolError(e.message)

    @mcp.tool(annotations=READ_ONLY, meta=ALWAYS_LOAD)
    async def drive_auth_status(ctx: Context) -> str:
        """Show which Google account is signed in and which folder is configured; signs in if needed."""
        async def work():
            email = await client.user_email()
            return f"Signed in as {email}. Configured folder: {config.folder}"
        return await call(ctx, "call drive_auth_status again", work)

    @mcp.tool(annotations=READ_ONLY, meta=ALWAYS_LOAD)
    async def drive_list_folder(ctx: Context, folder: str | None = None) -> str:
        """List every file in the configured Google Drive folder and its subfolders (path, id, type,
        size, modified time). `folder` may name a subfolder inside it by id or URL; leave it empty
        for the whole folder."""
        async def work():
            listing = await reader.list_folder()
            if folder and file_id_from(folder) != reader.root_id:
                sub = next((f for f in listing["files"] if f["id"] == file_id_from(folder)
                            and f["mimeType"] == "application/vnd.google-apps.folder"), None)
                if sub is None:
                    raise DriveError(403, "That folder is not inside the configured folder; only the configured "
                                          "folder and its subfolders can be listed (change it in the plugin settings).")
                listing["files"] = [f for f in listing["files"] if f["path"].startswith(sub["path"])]
            return json.dumps(listing, ensure_ascii=False, indent=1)
        return await call(ctx, "call drive_list_folder again", work)

    @mcp.tool(annotations=READ_ONLY, meta=ALWAYS_LOAD)
    async def drive_read_file(ctx: Context, file: str) -> list:
        """Read one file from the configured folder by id (from drive_list_folder) or Drive URL."""
        async def work():
            return render(await reader.read_file(file))
        result = await call(ctx, "call drive_read_file again", work)
        return [types.TextContent(type="text", text=result)] if isinstance(result, str) else result

    return mcp


def render(r: dict) -> list:
    f = r["file"]
    head = f"{f['name']} ({f['mimeType']}, id {f['id']})"
    if r["kind"] == "text":
        note = f"\n[{r['note']}]" if r.get("note") else ""
        return [types.TextContent(type="text", text=f"{head}{note}\n\n{r['text']}")]
    if r["kind"] == "image":
        return [types.TextContent(type="text", text=head),
                types.ImageContent(type="image", data=base64.b64encode(r["data"]).decode(), mimeType=r["mimeType"])]
    if r["kind"] == "pdf_blob":
        pages = r.get("pages")
        how = ("Claude Code saves the attached PDF to a file and shows its path: open that path with the "
               "Read tool to read the pages (they may be scanned images).")
        if pages and pages > 10:
            how += " It has more than 10 pages, so read it in ranges with the `pages` parameter."
        return [types.TextContent(type="text", text=f"{head}\n[{r['note']}] {how}"),
                types.EmbeddedResource(type="resource", resource=types.BlobResourceContents(
                    uri=f"gdrive://file/{f['id']}", mimeType="application/pdf",
                    blob=base64.b64encode(r["data"]).decode()))]
    return [types.TextContent(type="text", text=f"{head}\n{r['message']}")]


def main() -> None:
    build(Config.from_env()).run()
