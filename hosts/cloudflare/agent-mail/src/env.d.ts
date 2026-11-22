// Bindings that `wrangler types` can't see: the secret is set with
// `wrangler secret put AGENT_TOKEN_HASHES`, never in wrangler.jsonc.
interface Env {
	AGENT_TOKEN_HASHES?: string;
}
declare namespace Cloudflare {
	interface Env {
		AGENT_TOKEN_HASHES?: string;
	}
}
