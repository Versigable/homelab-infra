// KV layout. One key per message: m:<agent>:<id>, where <id> starts with an
// inverted millisecond timestamp so a prefix list returns newest first.

import type { AuthResults } from "./extract";

export interface StoredMessage {
	v: 1;
	id: string;
	agent: string;
	tag: string | null;
	receivedAt: string; // ISO, when the Worker stored it
	envelopeFrom: string; // SMTP MAIL FROM
	to: string; // SMTP RCPT TO
	from: { address: string; domain: string; name: string }; // From header; name is sender-controlled text
	subject: string; // sender-controlled text
	date: string | null; // Date header as sent
	auth: AuthResults;
	codes: string[];
	links: string[];
	text: string; // sender-controlled text, size-capped, never returned without ?full=1
	textTruncated: boolean;
	rawSize: number;
	attachments: number; // count only; attachment bodies are never stored
	parseError?: true;
}

/** KV list metadata (must stay under 1024 bytes). Enough to filter without a read. */
export interface ListMeta {
	r: number; // receivedAt, epoch ms
	f: string; // From address
	d: string; // From domain
	t: string | null; // +tag
	a: string; // DMARC verdict ("unknown" when not trusted)
	c: string[]; // codes
}

const MAX_TS = 9_999_999_999_999;
const ID_RE = /^\d{13}-[0-9a-f]{16}$/;

export function newId(nowMs: number): string {
	const inv = String(MAX_TS - nowMs).padStart(13, "0");
	const rand = [...crypto.getRandomValues(new Uint8Array(8))].map((b) => b.toString(16).padStart(2, "0")).join("");
	return `${inv}-${rand}`;
}

export function isValidId(id: string): boolean {
	return ID_RE.test(id);
}

export const keyFor = (agent: string, id: string) => `m:${agent}:${id}`;
export const prefixFor = (agent: string) => `m:${agent}:`;

export function metaFor(msg: StoredMessage): ListMeta {
	return {
		r: Date.parse(msg.receivedAt),
		f: msg.from.address.slice(0, 254),
		d: msg.from.domain.slice(0, 253),
		t: msg.tag,
		a: msg.auth.trusted ? msg.auth.dmarc : "unknown",
		c: msg.codes.map((c) => c.slice(0, 17)),
	};
}
