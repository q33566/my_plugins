/** Folder listing and file reading over the parts of the Google Drive v3 API the server uses. */

export const FOLDER = "application/vnd.google-apps.folder";
const SHORTCUT = "application/vnd.google-apps.shortcut";
const FILE_FIELDS = "id,name,mimeType,size,modifiedTime,parents,shortcutDetails(targetId,targetMimeType)";
const API = "https://www.googleapis.com";

const RETRY_STATUSES = new Set([429, 500, 502, 503, 504]);
const RATE_LIMIT_REASONS = new Set(["rateLimitExceeded", "userRateLimitExceeded"]); // Drive also sends these as 403

const EXPORTS: Record<string, [string, string | null]> = {
  "application/vnd.google-apps.document": ["text/plain", null],
  "application/vnd.google-apps.spreadsheet": ["text/csv", "Exported as CSV: only the first sheet is included."],
  "application/vnd.google-apps.presentation": ["text/plain", null],
};
const IMAGES = new Set(["image/png", "image/jpeg", "image/gif", "image/webp"]);
const TEXT_TYPES = new Set(["application/json", "application/xml", "application/javascript", "application/x-yaml"]);

/** A Drive request failed; `message` is safe to show to the user. */
export class DriveError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

export interface DriveFile {
  id: string;
  name: string;
  mimeType: string;
  size?: string;
  modifiedTime?: string;
  parents?: string[];
  shortcutDetails?: { targetId?: string; targetMimeType?: string };
}

export interface Entry {
  name: string;
  id: string;
  mimeType: string;
  modifiedTime?: string;
  size?: number;
  shortcut?: true;
  path: string;
}

export interface FileInfo { id: string; name: string; mimeType: string }

export type ReadResult =
  | { kind: "text"; file: FileInfo; text: string; note?: string }
  | { kind: "image" | "pdf"; file: FileInfo; data: ArrayBuffer; mimeType: string }
  | { kind: "unsupported" | "too_large"; file: FileInfo; message: string };

type Sleep = (ms: number) => Promise<void>;

export class DriveClient {
  maxAttempts = 5;

  constructor(private token: string, private sleep: Sleep = (ms) => new Promise((r) => setTimeout(r, ms))) {}

  private async get(path: string, params: Record<string, string>): Promise<Response> {
    const url = `${API}${path}?${new URLSearchParams(params)}`;
    let resp: Response | undefined;
    for (let attempt = 0; attempt < this.maxAttempts; attempt++) {
      try {
        resp = await fetch(url, { headers: { authorization: `Bearer ${this.token}` } });
      } catch (e) {
        if (attempt === this.maxAttempts - 1) {
          throw new DriveError(0, `Could not reach Google Drive (${(e as Error).name}); try again.`);
        }
        await this.sleep(backoff(attempt));
        continue;
      }
      const retryable = RETRY_STATUSES.has(resp.status) || (resp.status === 403 && RATE_LIMIT_REASONS.has(await reason(resp)));
      if (retryable && attempt < this.maxAttempts - 1) {
        await this.sleep(backoff(attempt));
        continue;
      }
      if (resp.status >= 400) throw new DriveError(resp.status, await explain(resp));
      return resp;
    }
    throw new DriveError(resp!.status, await explain(resp!));
  }

  async file(id: string): Promise<DriveFile> {
    return (await this.get(`/drive/v3/files/${id}`, { fields: FILE_FIELDS, supportsAllDrives: "true" })).json();
  }

  async userEmail(): Promise<string> {
    const body = (await (await this.get("/drive/v3/about", { fields: "user(emailAddress)" })).json()) as { user?: { emailAddress?: string } };
    return body.user?.emailAddress ?? "unknown account";
  }

  async download(id: string): Promise<ArrayBuffer> {
    return (await this.get(`/drive/v3/files/${id}`, { alt: "media", supportsAllDrives: "true" })).arrayBuffer();
  }

  async export(id: string, mime: string): Promise<string> {
    return (await this.get(`/drive/v3/files/${id}/export`, { mimeType: mime })).text();
  }

  async children(folderId: string): Promise<DriveFile[]> {
    const out: DriveFile[] = [];
    let pageToken: string | undefined;
    do {
      const params: Record<string, string> = {
        q: `'${folderId}' in parents and trashed = false`,
        fields: `nextPageToken,files(${FILE_FIELDS})`,
        pageSize: "1000",
        supportsAllDrives: "true",
        includeItemsFromAllDrives: "true",
      };
      if (pageToken) params.pageToken = pageToken;
      const body = (await (await this.get("/drive/v3/files", params)).json()) as { files?: DriveFile[]; nextPageToken?: string };
      out.push(...(body.files ?? []));
      pageToken = body.nextPageToken;
    } while (pageToken);
    return out;
  }
}

/** Accept a bare id or any drive.google.com / docs.google.com URL. */
export function fileIdFrom(ref: string): string {
  ref = ref.trim();
  const m = /\/(?:d|folders)\/([A-Za-z0-9_-]+)/.exec(ref) ?? /[?&]id=([A-Za-z0-9_-]+)/.exec(ref);
  return m ? m[1] : ref;
}

const mb = (n: number) => `${(n / (1024 * 1024)).toFixed(1)} MB`;

export class DriveReader {
  maxItems = 2000;
  maxDepth = 20;
  // A tool result travels base64-encoded inside one HTTP response (Claude Code caps it at 16 MiB).
  maxDownload = 10 * 1024 * 1024;
  maxImage = 5 * 1024 * 1024;
  maxTextChars = 200_000;
  readonly rootId: string;

  constructor(private client: DriveClient, root: string) {
    this.rootId = fileIdFrom(root);
  }

  async listFolder(): Promise<{ folder: { id: string; name: string }; files: Entry[]; truncated?: true }> {
    const root = await this.client.file(this.rootId);
    if (root.mimeType !== FOLDER) throw new DriveError(400, `"${root.name}" is not a folder.`);
    const walk = new Walk(this);
    await walk.folder(this.rootId, "", 0);
    const result: { folder: { id: string; name: string }; files: Entry[]; truncated?: true } = { folder: { id: root.id, name: root.name }, files: walk.out };
    if (walk.truncated) result.truncated = true;
    return result;
  }

  async readFile(ref: string): Promise<ReadResult> {
    const id = fileIdFrom(ref);
    const listing = await this.listFolder(); // the only proof a file is inside the folder; it may have changed
    if (!listing.files.some((f) => f.id === id)) {
      throw new DriveError(403, `That file is outside the folder "${listing.folder.name}"; only files listed in that folder can be read.`);
    }
    const meta = await this.client.file(id);
    const file = { id: meta.id, name: meta.name, mimeType: meta.mimeType };
    const mime = meta.mimeType;
    const size = Number(meta.size ?? 0);
    if (mime === FOLDER) return { kind: "unsupported", file, message: `"${meta.name}" is a folder; list it to see its files.` };
    if (mime in EXPORTS) {
      const [exportMime, note] = EXPORTS[mime];
      return this.text(file, await this.client.export(id, exportMime), note);
    }
    if (mime.startsWith("application/vnd.google-apps.")) {
      return { kind: "unsupported", file, message: `"${meta.name}" is a Google ${mime.split(".").pop()} file, which cannot be read as text.` };
    }
    const isText = mime.startsWith("text/") || TEXT_TYPES.has(mime);
    if (!IMAGES.has(mime) && mime !== "application/pdf" && !isText) {
      return { kind: "unsupported", file, message: `"${meta.name}" has type ${mime}, which this server cannot read.` };
    }
    const cap = IMAGES.has(mime) ? this.maxImage : this.maxDownload;
    if (size > cap) return { kind: "too_large", file, message: `"${meta.name}" is ${mb(size)}; the limit for this type is ${mb(cap)}.` };
    const data = await this.client.download(id);
    if (IMAGES.has(mime)) return { kind: "image", file, data, mimeType: mime };
    if (mime === "application/pdf") return { kind: "pdf", file, data, mimeType: mime };
    return this.text(file, new TextDecoder().decode(data), null);
  }

  private text(file: FileInfo, text: string, note: string | null): ReadResult {
    if (text.length > this.maxTextChars) {
      text = text.slice(0, this.maxTextChars);
      note = (note ? note + " " : "") + `Truncated to the first ${this.maxTextChars.toLocaleString("en")} characters.`;
    }
    return note ? { kind: "text", file, text, note } : { kind: "text", file, text };
  }
}

class Walk {
  out: Entry[] = [];
  truncated = false;
  private visited: Set<string>;

  constructor(private reader: DriveReader) {
    this.visited = new Set([reader.rootId]);
  }

  async folder(folderId: string, prefix: string, depth: number): Promise<void> {
    if (depth >= this.reader.maxDepth) {
      this.truncated = true;
      return;
    }
    for (const f of await this.reader["client"].children(folderId)) {
      if (this.out.length >= this.reader.maxItems) {
        this.truncated = true;
        return;
      }
      const entry: Entry = { name: f.name, id: f.id, mimeType: f.mimeType, modifiedTime: f.modifiedTime, path: "" };
      if (f.mimeType === SHORTCUT) {
        entry.id = f.shortcutDetails?.targetId ?? f.id;
        entry.mimeType = f.shortcutDetails?.targetMimeType ?? f.mimeType;
        entry.shortcut = true;
      } else if (f.size !== undefined) {
        entry.size = Number(f.size);
      }
      const isFolder = entry.mimeType === FOLDER;
      entry.path = prefix + f.name + (isFolder ? "/" : "");
      this.out.push(entry);
      if (isFolder && !this.visited.has(entry.id)) { // a shortcut back is listed, not entered
        this.visited.add(entry.id);
        await this.folder(entry.id, entry.path, depth + 1);
      }
    }
  }
}

function backoff(attempt: number): number {
  return Math.min(8000, 500 * 2 ** attempt) * (0.5 + Math.random() / 2);
}

async function errorBody(resp: Response): Promise<{ error?: { message?: string; errors?: { reason?: string }[] } } | null> {
  try {
    return await resp.clone().json();
  } catch {
    return null;
  }
}

async function reason(resp: Response): Promise<string> {
  return (await errorBody(resp))?.error?.errors?.[0]?.reason ?? "";
}

async function explain(resp: Response): Promise<string> {
  const body = await errorBody(resp);
  const detail = body?.error?.message ?? `HTTP ${resp.status}`;
  const why = body?.error?.errors?.[0]?.reason ?? "";
  const s = resp.status;
  if (s === 401) return `Google rejected the sign-in (${detail}). Run /mcp in Claude Code and sign in to gdrive-reader again.`;
  if (s === 403 && why === "exportSizeLimitExceeded") {
    return "This Google file is too large to export as text (Google's export limit is 10 MB). Open it in Google Drive instead.";
  }
  if (s === 403 && RATE_LIMIT_REASONS.has(why)) return `Google Drive is rate-limiting requests (${detail}); try again in a minute.`;
  if (s === 403) {
    return `Google Drive refused access (${detail}). Check that this Google account can open the folder, and that Drive read access was granted when signing in.`;
  }
  if (s === 404) return `File or folder not found (${detail}). It may have been deleted, or this Google account has no access to it.`;
  if (RETRY_STATUSES.has(s)) return `Google Drive is busy or unavailable (HTTP ${s}: ${detail}); try again in a minute.`;
  return `Google Drive request failed (HTTP ${s}: ${detail}).`;
}
