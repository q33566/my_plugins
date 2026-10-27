/** An in-memory KVNamespace with the calls @cloudflare/workers-oauth-provider makes. */
export function fakeKV(): KVNamespace {
  const store = new Map<string, { value: string; expires?: number; metadata?: unknown }>();
  const live = (key: string) => {
    const e = store.get(key);
    if (e?.expires && e.expires <= Date.now()) store.delete(key);
    return store.get(key);
  };
  return {
    async get(key: string, type?: unknown) {
      const e = live(key);
      if (!e) return null;
      const t = typeof type === "string" ? type : (type as { type?: string } | undefined)?.type;
      return t === "json" ? JSON.parse(e.value) : e.value;
    },
    async getWithMetadata(key: string, type?: unknown) {
      const e = live(key);
      if (!e) return { value: null, metadata: null };
      const t = typeof type === "string" ? type : (type as { type?: string } | undefined)?.type;
      return { value: t === "json" ? JSON.parse(e.value) : e.value, metadata: e.metadata ?? null };
    },
    async put(key: string, value: string, opts?: { expirationTtl?: number; expiration?: number; metadata?: unknown }) {
      const expires = opts?.expirationTtl ? Date.now() + opts.expirationTtl * 1000 : opts?.expiration ? opts.expiration * 1000 : undefined;
      store.set(key, { value: String(value), expires, metadata: opts?.metadata });
    },
    async delete(key: string) {
      store.delete(key);
    },
    async list(opts?: { prefix?: string; limit?: number; cursor?: string }) {
      const keys = [...store.keys()].filter((k) => live(k) && k.startsWith(opts?.prefix ?? "")).sort();
      const start = Number(opts?.cursor ?? 0);
      const limit = opts?.limit ?? 1000;
      const page = keys.slice(start, start + limit);
      const done = start + limit >= keys.length;
      return { keys: page.map((name) => ({ name, metadata: store.get(name)?.metadata })), list_complete: done, cursor: done ? undefined : String(start + limit) };
    },
  } as unknown as KVNamespace;
}
