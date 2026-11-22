// Pure helpers: no bindings, no I/O. Everything here handles mail content as
// hostile data. It is parsed and trimmed, never interpreted.

export const LIMITS = {
	textBytes: 32 * 1024, // stored body text, after HTML stripping
	codeSearchChars: 8 * 1024, // codes are near the top of real mail
	subjectChars: 300,
	nameChars: 100,
	addressChars: 254,
	maxCodes: 5,
	maxLinks: 20,
	linkChars: 2048,
} as const;

// ---------------------------------------------------------------- recipients

export interface Recipient {
	agent: string;
	tag: string | null;
}

const AGENT_RE = /^[a-z0-9][a-z0-9._-]{0,63}$/;
const TAG_RE = /^[a-z0-9._-]{1,64}$/;

/** `MV-Agent+BW@agents.example.org` -> { agent: "mv-agent", tag: "bw" }. Null if not ours. */
export function splitRecipient(to: string, domain: string): Recipient | null {
	const addr = to.trim().replace(/^<|>$/g, "").toLowerCase();
	const at = addr.lastIndexOf("@");
	if (at <= 0 || addr.slice(at + 1) !== domain.toLowerCase()) return null;
	const local = addr.slice(0, at);
	const plus = local.indexOf("+");
	const agent = plus >= 0 ? local.slice(0, plus) : local;
	if (!AGENT_RE.test(agent)) return null;
	const rawTag = plus >= 0 ? local.slice(plus + 1) : "";
	return { agent, tag: TAG_RE.test(rawTag) ? rawTag : null };
}

export function parseAgentList(csv: string | undefined): Set<string> {
	return new Set(
		(csv ?? "")
			.split(",")
			.map((s) => s.trim().toLowerCase())
			.filter((s) => AGENT_RE.test(s)),
	);
}

export function domainOf(address: string | undefined): string {
	const a = (address ?? "").trim().toLowerCase();
	const at = a.lastIndexOf("@");
	return at >= 0 ? a.slice(at + 1).replace(/>$/, "") : "";
}

// ------------------------------------------------------ authentication results

export interface AuthResults {
	/** True only when the first Authentication-Results header is Cloudflare's own. */
	trusted: boolean;
	spf: string;
	dkim: string;
	dmarc: string;
}

const UNKNOWN_AUTH: AuthResults = { trusted: false, spf: "unknown", dkim: "unknown", dmarc: "unknown" };

/**
 * Email Routing prepends its own Authentication-Results header, with the
 * authserv-id `mx.cloudflare.net`, before invoking the Worker. Only that first
 * header is trusted; any lower ones may have been written by the sender.
 * `headers` is in message order, top first (postal-mime's `headers`).
 */
export function parseAuthResults(headers: ReadonlyArray<{ key: string; value: string }>): AuthResults {
	const first = headers.find((h) => h.key.toLowerCase() === "authentication-results");
	if (!first) return UNKNOWN_AUTH;
	const value = first.value.replace(/\([^()]*\)/g, " ").replace(/\s+/g, " ").trim();
	const [servPart, ...rest] = value.split(";");
	const servId = (servPart ?? "").trim().split(" ")[0]?.toLowerCase();
	if (servId !== "mx.cloudflare.net") return UNKNOWN_AUTH;
	const body = rest.join(";");
	const results = (method: string): string[] =>
		[...body.matchAll(new RegExp(`(?:^|;)\\s*${method}\\s*=\\s*([a-z]+)`, "gi"))].map((m) => m[1]!.toLowerCase());
	const pick = (method: string): string => {
		const all = results(method);
		if (all.length === 0) return "none";
		return all.includes("pass") ? "pass" : all[0]!;
	};
	return { trusted: true, spf: pick("spf"), dkim: pick("dkim"), dmarc: pick("dmarc") };
}

// -------------------------------------------------------------------- text

const ENTITIES: Record<string, string> = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " " };

export function decodeEntities(s: string): string {
	return s.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (whole, ent: string) => {
		if (ent[0] === "#") {
			const cp = ent[1] === "x" || ent[1] === "X" ? parseInt(ent.slice(2), 16) : parseInt(ent.slice(1), 10);
			return Number.isFinite(cp) && cp > 0 && cp <= 0x10ffff ? String.fromCodePoint(cp) : whole;
		}
		return ENTITIES[ent.toLowerCase()] ?? whole;
	});
}

/** Crude HTML -> text for mail with no text/plain part. Output is data, never markup. */
export function htmlToText(html: string): string {
	return decodeEntities(
		html
			.replace(/<(script|style|head)\b[^]*?<\/\1\s*>/gi, " ")
			.replace(/<!--[^]*?-->/g, " ")
			.replace(/<(br|\/p|\/div|\/tr|\/li|\/h[1-6])\b[^>]*>/gi, "\n")
			.replace(/<[^>]+>/g, " "),
	)
		.replace(/[ \t\f\v ]+/g, " ")
		.replace(/ *\n */g, "\n")
		.replace(/\n{3,}/g, "\n\n")
		.trim();
}

/** Cut to at most `maxBytes` of UTF-8 without splitting a character. */
export function truncateUtf8(s: string, maxBytes: number): { text: string; truncated: boolean } {
	const bytes = new TextEncoder().encode(s);
	if (bytes.byteLength <= maxBytes) return { text: s, truncated: false };
	let end = maxBytes;
	while (end > 0 && (bytes[end]! & 0xc0) === 0x80) end--; // back off continuation bytes
	return { text: new TextDecoder().decode(bytes.subarray(0, end)), truncated: true };
}

export function clip(s: string | undefined, max: number): string {
	const t = (s ?? "").replace(/[\u0000-\u001f\u007f]+/g, " ").trim();
	return t.length > max ? t.slice(0, max) : t;
}

// ------------------------------------------------------------------- codes

const CODE_KEYWORD =
	/\b(code|codes|verification|verify|otp|one[- ]time|passcode|pin|security|login|sign[- ]?in|2fa|token|confirm(?:ation)?)\b/gi;

// Digits (4-8, or 3+3 split by a space or hyphen), or upper-case letters+digits.
const DIGIT_CODE = /(?<![\w$€£.,/:#@-])(\d{3}[ -]\d{3}|\d{4,8})(?![\w%/:@-]|[.,]\d)/g;
const ALNUM_CODE = /(?<![\w./:#@-])(?=[A-Z0-9-]*\d)(?=[A-Z0-9-]*[A-Z])([A-Z0-9]{5,8}|[A-Z0-9]{3,5}-[A-Z0-9]{3,5})(?![\w/:@-])/g;

/**
 * Best-effort one-time codes, most likely first. A candidate only counts when
 * it sits near a word like "code" or "verification"; bare numbers in mail are
 * mostly prices, dates and order numbers.
 */
export function extractCodes(subject: string, text: string): string[] {
	const hay = `${subject}\n${text.slice(0, LIMITS.codeSearchChars)}`;
	const keywordAt = [...hay.matchAll(CODE_KEYWORD)].map((m) => m.index!);
	const near = (pos: number) => keywordAt.some((k) => pos - k <= 80 && k - pos <= 40);
	const found = new Map<string, { score: number; pos: number }>();
	const consider = (raw: string, pos: number, alnum: boolean) => {
		if (!near(pos)) return;
		const code = alnum ? raw : raw.replace(/[ -]/g, "");
		let score = 10;
		if (pos < subject.length) score += 3;
		if (code.length === 6) score += 2;
		if (!alnum && /^(19|20)\d\d$/.test(code)) score -= 8; // a year
		if (score < 5) return;
		const prev = found.get(code);
		if (!prev || prev.score < score) found.set(code, { score, pos: prev?.pos ?? pos });
	};
	for (const m of hay.matchAll(DIGIT_CODE)) consider(m[1]!, m.index!, false);
	for (const m of hay.matchAll(ALNUM_CODE)) consider(m[1]!, m.index!, true);
	return [...found.entries()]
		.sort((a, b) => b[1].score - a[1].score || a[1].pos - b[1].pos)
		.slice(0, LIMITS.maxCodes)
		.map(([code]) => code);
}

// ------------------------------------------------------------------- links

const ACTION_HINT = /(verif|confirm|activat|invit|accept|reset|magic|login|log-in|signin|sign-in|sign_in|token|join|validat|onboard|register)/i;
const NOISE_HINT = /(unsubscribe|preferences|privacy|terms|legal|facebook\.|twitter\.|x\.com|linkedin\.|instagram\.|youtube\.|tiktok\.)/i;

function cleanUrl(raw: string): string | null {
	let s = decodeEntities(raw.trim()).replace(/[.,;:!?'"]+$/, "");
	// Drop an unmatched trailing ")" left over from prose like "(see https://x/y)".
	while (s.endsWith(")") && (s.match(/\(/g)?.length ?? 0) < (s.match(/\)/g)?.length ?? 0)) s = s.slice(0, -1);
	if (s.length > LIMITS.linkChars) return null;
	try {
		const u = new URL(s);
		if (u.protocol !== "https:" && u.protocol !== "http:") return null;
		if (u.username || u.password) return null;
		return u.toString();
	} catch {
		return null;
	}
}

/** http(s) links from <a href> and from plain text, action links first, footer noise last. */
export function extractLinks(text: string, html: string | undefined): string[] {
	const candidates: string[] = [];
	if (html) {
		for (const m of html.matchAll(/<a\b[^>]*?\bhref\s*=\s*("([^"]*)"|'([^']*)'|([^\s>]+))/gi)) {
			candidates.push(m[2] ?? m[3] ?? m[4] ?? "");
		}
	}
	for (const m of text.matchAll(/https?:\/\/[^\s<>"'\[\]{}|\\^`]+/gi)) candidates.push(m[0]);

	const seen = new Set<string>();
	const ranked: { url: string; score: number; pos: number }[] = [];
	candidates.forEach((raw, pos) => {
		const url = cleanUrl(raw);
		if (!url || seen.has(url)) return;
		seen.add(url);
		const score = (ACTION_HINT.test(url) ? 10 : 0) - (NOISE_HINT.test(url) ? 10 : 0);
		ranked.push({ url, score, pos });
	});
	return ranked
		.sort((a, b) => b.score - a.score || a.pos - b.pos)
		.slice(0, LIMITS.maxLinks)
		.map((r) => r.url);
}
