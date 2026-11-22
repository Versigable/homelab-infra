// Per-agent bearer tokens. The Worker only ever holds SHA-256 hashes, in the
// AGENT_TOKEN_HASHES secret: {"mv-agent":"<64 hex>", ...}. Tokens are 256-bit
// random strings (scripts/mint-token.mjs), so a plain hash is enough; there is
// nothing to brute-force.

const HEX64 = /^[0-9a-f]{64}$/;
const AGENT_RE = /^[a-z0-9][a-z0-9._-]{0,63}$/;

export function parseTokenHashes(raw: string | undefined): Map<string, string> {
	const out = new Map<string, string>();
	if (!raw) return out;
	let obj: unknown;
	try {
		obj = JSON.parse(raw);
	} catch {
		console.error(JSON.stringify({ event: "config_error", what: "AGENT_TOKEN_HASHES is not JSON" }));
		return out;
	}
	if (!obj || typeof obj !== "object" || Array.isArray(obj)) return out;
	for (const [agent, hash] of Object.entries(obj as Record<string, unknown>)) {
		if (AGENT_RE.test(agent) && typeof hash === "string" && HEX64.test(hash.toLowerCase())) {
			out.set(agent, hash.toLowerCase());
		}
	}
	return out;
}

export async function sha256Hex(s: string): Promise<string> {
	const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
	return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function sameHex(a: string, b: string): boolean {
	const ea = new TextEncoder().encode(a);
	const eb = new TextEncoder().encode(b);
	if (ea.byteLength !== eb.byteLength) return false;
	return crypto.subtle.timingSafeEqual(ea, eb);
}

/** The agent a request's bearer token belongs to, or null. Compares against every hash. */
export async function authenticate(request: Request, hashes: Map<string, string>): Promise<string | null> {
	const header = request.headers.get("authorization") ?? "";
	const m = /^Bearer ([A-Za-z0-9_\-.~+/=]{20,200})$/.exec(header);
	if (!m || hashes.size === 0) return null;
	const presented = await sha256Hex(m[1]!);
	let match: string | null = null;
	for (const [agent, hash] of hashes) {
		if (sameHex(presented, hash) && match === null) match = agent;
	}
	return match;
}
