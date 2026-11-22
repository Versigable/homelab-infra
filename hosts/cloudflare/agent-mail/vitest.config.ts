import { createHash } from "node:crypto";
import { cloudflareTest } from "@cloudflare/vitest-plugin";
import { defineConfig } from "vitest/config";
import { TOKENS } from "./test/fixtures.ts";

const hashes = Object.fromEntries(
	Object.entries(TOKENS).map(([agent, token]) => [agent, createHash("sha256").update(token).digest("hex")]),
);

export default defineConfig({
	plugins: [
		cloudflareTest({
			wrangler: { configPath: "./wrangler.jsonc" },
			// Local, in-memory KV and test-only secrets. Nothing here reaches Cloudflare.
			miniflare: { bindings: { AGENT_TOKEN_HASHES: JSON.stringify(hashes) } },
		}),
	],
});
