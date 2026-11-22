// email() handler: accept mail for known agents, store a trimmed record, keep nothing else.

import PostalMime from "postal-mime";
import {
	LIMITS,
	clip,
	domainOf,
	extractCodes,
	extractLinks,
	htmlToText,
	parseAgentList,
	parseAuthResults,
	splitRecipient,
	truncateUtf8,
	type AuthResults,
} from "./extract";
import { keyFor, metaFor, newId, type StoredMessage } from "./store";

/** Larger mail is refused. Verification mail is tens of KB; Email Routing's own cap is 25 MiB. */
export const MAX_RAW_BYTES = 10 * 1024 * 1024;

export async function handleEmail(message: ForwardableEmailMessage, env: Env): Promise<void> {
	const rcpt = splitRecipient(message.to, env.MAIL_DOMAIN);
	if (!rcpt || !parseAgentList(env.AGENTS).has(rcpt.agent)) {
		// Second layer: Email Routing only sends literal per-agent addresses here.
		message.setReject("Address does not exist");
		console.log(JSON.stringify({ event: "rejected", reason: "unknown_recipient" }));
		return;
	}
	if (message.rawSize > MAX_RAW_BYTES) {
		message.setReject("Message too large");
		console.log(JSON.stringify({ event: "rejected", reason: "too_large", agent: rcpt.agent, size: message.rawSize }));
		return;
	}

	const now = Date.now();
	const base = {
		v: 1 as const,
		id: newId(now),
		agent: rcpt.agent,
		tag: rcpt.tag,
		receivedAt: new Date(now).toISOString(),
		envelopeFrom: clip(message.from, LIMITS.addressChars),
		to: clip(message.to, LIMITS.addressChars),
		rawSize: message.rawSize,
	};

	let record: StoredMessage;
	try {
		const parsed = await PostalMime.parse(message.raw, { attachmentEncoding: "arraybuffer" });
		const subject = clip(parsed.subject, LIMITS.subjectChars);
		const fullText = parsed.text?.trim() ? parsed.text : parsed.html ? htmlToText(parsed.html) : "";
		const { text, truncated } = truncateUtf8(fullText, LIMITS.textBytes);
		const fromAddress = clip(parsed.from?.address ?? message.from, LIMITS.addressChars).toLowerCase();
		record = {
			...base,
			from: { address: fromAddress, domain: domainOf(fromAddress), name: clip(parsed.from?.name, LIMITS.nameChars) },
			subject,
			date: parsed.date ? clip(parsed.date, 64) : null,
			auth: parseAuthResults(parsed.headers),
			codes: extractCodes(subject, fullText),
			links: extractLinks(fullText, parsed.html),
			text,
			textTruncated: truncated,
			attachments: parsed.attachments.length,
		};
	} catch (err) {
		// Keep the arrival on record rather than lose it; there is just nothing to extract.
		const unknown: AuthResults = { trusted: false, spf: "unknown", dkim: "unknown", dmarc: "unknown" };
		const fromAddress = base.envelopeFrom.toLowerCase();
		record = {
			...base,
			from: { address: fromAddress, domain: domainOf(fromAddress), name: "" },
			subject: "",
			date: null,
			auth: unknown,
			codes: [],
			links: [],
			text: "",
			textTruncated: false,
			attachments: 0,
			parseError: true,
		};
		console.error(JSON.stringify({ event: "parse_error", agent: rcpt.agent, error: String(err).slice(0, 200) }));
	}

	const ttlDays = Number.parseInt(env.RETENTION_DAYS, 10);
	await env.MAIL.put(keyFor(record.agent, record.id), JSON.stringify(record), {
		expirationTtl: (Number.isFinite(ttlDays) && ttlDays > 0 ? ttlDays : 30) * 86_400,
		metadata: metaFor(record),
	});
	console.log(
		JSON.stringify({
			event: "stored",
			agent: record.agent,
			size: record.rawSize,
			dmarc: record.auth.trusted ? record.auth.dmarc : "unknown",
			codes: record.codes.length,
			links: record.links.length,
		}),
	);
}
