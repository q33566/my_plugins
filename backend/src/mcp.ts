/** The MCP server: read-only tools over one Google Drive folder, built fresh for each request. */
import { Buffer } from "node:buffer";
import { McpServer } from "@modelcontextprotocol/server";
import { getMcpAuthContext } from "agents/mcp/server";
import { z } from "zod";
import { DriveClient, DriveError, DriveReader, type ReadResult } from "./drive";

/** What a grant carries (encrypted by the OAuth provider): the Google account and its tokens. */
export interface Props {
  email: string;
  googleAccessToken: string;
  googleRefreshToken: string;
  [key: string]: unknown;
}

const INSTRUCTIONS = `Read-only access to a Google Drive folder and its subfolders. Use these tools for any \
request about "the Drive folder", "the folder", or the files in it. Without a folder argument they use the \
default folder; pass a Drive folder URL or id to use another folder the signed-in account can open.

- drive_list_folder: every file in the folder tree with path, id, type and size.
- drive_read_file: one file's content by id or Drive URL. Docs, Sheets (first sheet), Slides and text files \
come back as text; images as images; a PDF as a file Claude Code saves: open that path with Read.
- drive_auth_status: which Google account is signed in, and the default folder.

Only files inside the folder can be read. There are no write, search or watch tools. If a tool says the \
sign-in was rejected, ask the user to run /mcp and sign in to gdrive-reader again.`;

const READ_ONLY = { readOnlyHint: true, destructiveHint: false, openWorldHint: true };
const META = { "anthropic/alwaysLoad": true };

type Content =
  | { type: "text"; text: string }
  | { type: "image"; data: string; mimeType: string }
  | { type: "resource"; resource: { uri: string; mimeType: string; blob: string } };

export function createServer(defaultFolder: string) {
  return () => {
    const server = new McpServer({ name: "gdrive-reader", version: "0.2.0" }, { instructions: INSTRUCTIONS });
    const props = () => getMcpAuthContext()?.props as Props | undefined;
    const reader = (folder?: string) => {
      const p = props();
      if (!p?.googleAccessToken) throw new DriveError(401, "Not signed in. Run /mcp in Claude Code and sign in to gdrive-reader.");
      return new DriveReader(new DriveClient(p.googleAccessToken), folder?.trim() || defaultFolder);
    };

    server.registerTool(
      "drive_auth_status",
      {
        description: "Show which Google account is signed in and which folder is the default.",
        annotations: READ_ONLY,
        _meta: META,
      },
      async () => guard(async () => {
        const email = await new DriveClient(props()?.googleAccessToken ?? "").userEmail();
        return [text(`Signed in as ${email}. Default folder: ${defaultFolder}`)];
      }),
    );

    server.registerTool(
      "drive_list_folder",
      {
        description: "List every file in a Google Drive folder and its subfolders (path, id, type, size, modified time). " +
          "`folder` is a Drive folder URL or id; leave it empty for the default folder.",
        inputSchema: z.object({ folder: z.string().optional() }),
        annotations: READ_ONLY,
        _meta: META,
      },
      async ({ folder }) => guard(async () => [text(JSON.stringify(await reader(folder).listFolder(), null, 1))]),
    );

    server.registerTool(
      "drive_read_file",
      {
        description: "Read one file by id (from drive_list_folder) or Drive URL. `folder` is the folder it must be in; " +
          "leave it empty for the default folder.",
        inputSchema: z.object({ file: z.string(), folder: z.string().optional() }),
        annotations: READ_ONLY,
        _meta: META,
      },
      async ({ file, folder }) => guard(async () => render(await reader(folder).readFile(file))),
    );

    return server;
  };
}

async function guard(work: () => Promise<Content[]>) {
  try {
    return { content: await work() };
  } catch (e) {
    const message = e instanceof DriveError ? e.message : `Unexpected error: ${(e as Error).message}`;
    return { content: [text(message)], isError: true };
  }
}

const text = (t: string): Content => ({ type: "text", text: t });

export function render(r: ReadResult): Content[] {
  const f = r.file;
  const head = `${f.name} (${f.mimeType}, id ${f.id})`;
  switch (r.kind) {
    case "text":
      return [text(`${head}${r.note ? `\n[${r.note}]` : ""}\n\n${r.text}`)];
    case "image":
      return [text(head), { type: "image", data: Buffer.from(r.data).toString("base64"), mimeType: r.mimeType }];
    case "pdf":
      return [
        text(`${head}\nClaude Code saves the attached PDF to a file and shows its path: open that path with the Read ` +
          "tool to read it (for more than 10 pages, read it in ranges with the `pages` parameter)."),
        { type: "resource", resource: { uri: `gdrive://file/${f.id}`, mimeType: r.mimeType, blob: Buffer.from(r.data).toString("base64") } },
      ];
    default:
      return [text(`${head}\n${r.message}`)];
  }
}

