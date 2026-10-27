import { defineConfig } from "vitest/config";

const stub = new URL("./test/cloudflare-stub.ts", import.meta.url).pathname;

export default defineConfig({
  resolve: { alias: { "cloudflare:workers": stub, "cloudflare:email": stub } },
  test: { server: { deps: { inline: [/agents/, /workers-oauth-provider/, /@modelcontextprotocol/] } } },
});
