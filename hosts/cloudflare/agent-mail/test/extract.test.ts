import { describe, expect, it } from "vitest";
import {
	extractCodes,
	extractLinks,
	htmlToText,
	parseAgentList,
	parseAuthResults,
	splitRecipient,
	truncateUtf8,
} from "../src/extract";

describe("splitRecipient", () => {
	it("lower-cases and splits a +tag", () => {
		expect(splitRecipient("MV-Agent+BW@Agents.NinjaPrivacy.org", "agents.ninjaprivacy.org")).toEqual({
			agent: "mv-agent",
			tag: "bw",
		});
	});
	it("has no tag without a plus", () => {
		expect(splitRecipient("<merquery@agents.ninjaprivacy.org>", "agents.ninjaprivacy.org")).toEqual({
			agent: "merquery",
			tag: null,
		});
	});
	it("rejects other domains, including the apex", () => {
		expect(splitRecipient("mv-agent@ninjaprivacy.org", "agents.ninjaprivacy.org")).toBeNull();
		expect(splitRecipient("mv-agent@agents.ninjaprivacy.org.evil.com", "agents.ninjaprivacy.org")).toBeNull();
	});
	it("drops a tag with odd characters but keeps the agent", () => {
		expect(splitRecipient("mv-agent+a b@agents.ninjaprivacy.org", "agents.ninjaprivacy.org")).toEqual({
			agent: "mv-agent",
			tag: null,
		});
	});
});

describe("parseAgentList", () => {
	it("trims, lower-cases and drops junk", () => {
		expect([...parseAgentList(" MV-Agent, merquery ,,bad name")]).toEqual(["mv-agent", "merquery"]);
	});
});

describe("parseAuthResults", () => {
	const h = (...values: string[]) => values.map((value) => ({ key: "authentication-results", value }));

	it("trusts the first header when it is Cloudflare's", () => {
		expect(parseAuthResults(h("mx.cloudflare.net; spf=pass smtp.mailfrom=x.com; dkim=pass header.d=x.com; dmarc=pass"))).toEqual({
			trusted: true,
			spf: "pass",
			dkim: "pass",
			dmarc: "pass",
		});
	});
	it("ignores a forged header below Cloudflare's", () => {
		const r = parseAuthResults(h("mx.cloudflare.net; spf=fail; dkim=none; dmarc=fail", "mx.cloudflare.net; spf=pass; dkim=pass; dmarc=pass"));
		expect(r).toEqual({ trusted: true, spf: "fail", dkim: "none", dmarc: "fail" });
	});
	it("does not trust another service's header", () => {
		expect(parseAuthResults(h("mail.evil.com; dmarc=pass")).trusted).toBe(false);
	});
	it("does not trust a missing header", () => {
		expect(parseAuthResults([{ key: "subject", value: "x" }])).toMatchObject({ trusted: false, dmarc: "unknown" });
	});
	it("takes pass from any of several DKIM signatures, and ignores comments", () => {
		const r = parseAuthResults(h("mx.cloudflare.net (cloudflare); dkim=fail (bad sig) header.d=a.com; dkim=pass header.d=b.com; spf=softfail; dmarc=pass"));
		expect(r).toMatchObject({ dkim: "pass", spf: "softfail", dmarc: "pass" });
	});
});

describe("extractCodes", () => {
	it("finds a code after a keyword", () => {
		expect(extractCodes("Your sign-in", "Hello,\nYour verification code is 482913.\nIt expires soon.")).toEqual(["482913"]);
	});
	it("joins a split code from the subject", () => {
		expect(extractCodes("123 456 is your login code", "")).toEqual(["123456"]);
	});
	it("ignores numbers with no keyword nearby", () => {
		expect(extractCodes("Your order", "Order 55512 shipped on 2026-11-21. Total $1234.50 for 3 items.")).toEqual([]);
	});
	it("does not take a year as a code", () => {
		expect(extractCodes("", "Your code: 771204. Copyright 2026.")).toEqual(["771204"]);
	});
	it("finds an upper-case alphanumeric code", () => {
		expect(extractCodes("", "Enter this security code: X7K9QZ to continue")).toEqual(["X7K9QZ"]);
	});
	it("does not take phone numbers or prices", () => {
		expect(extractCodes("", "Questions about your code? Call 555-123-4567 or pay $123456.")).toEqual([]);
	});
	it("caps the number of codes", () => {
		const text = Array.from({ length: 9 }, (_, i) => `code ${100000 + i}`).join("\n");
		expect(extractCodes("", text)).toHaveLength(5);
	});
});

describe("extractLinks", () => {
	it("ranks action links first and footer noise last, skips images and non-http", () => {
		const html = `
			<a href="https://example.com/unsubscribe?u=1">Unsubscribe</a>
			<img src="https://track.example.com/pixel.gif">
			<a href='https://vault.example.com/#/accept-organization?token=abc&amp;id=9'>Join</a>
			<a href="javascript:alert(1)">x</a>
			<a href="mailto:help@example.com">mail</a>
			<a href=https://example.com/about>About</a>`;
		expect(extractLinks("", html)).toEqual([
			"https://vault.example.com/#/accept-organization?token=abc&id=9",
			"https://example.com/about",
			"https://example.com/unsubscribe?u=1",
		]);
	});
	it("trims trailing punctuation and dedups text links", () => {
		const text = "Confirm here: https://x.example/verify?t=1. Or (https://x.example/verify?t=1) again.";
		expect(extractLinks(text, undefined)).toEqual(["https://x.example/verify?t=1"]);
	});
	it("drops links with embedded credentials", () => {
		expect(extractLinks("https://user:pw@x.example/a", undefined)).toEqual([]);
	});
});

describe("text helpers", () => {
	it("htmlToText drops scripts and styles and decodes entities", () => {
		const t = htmlToText("<style>p{}</style><p>Hi &amp; welcome</p><script>evil()</script><div>Code&nbsp;123</div>");
		expect(t).toBe("Hi & welcome\nCode 123");
	});
	it("truncateUtf8 never splits a character", () => {
		const r = truncateUtf8("ééé", 5); // 6 bytes
		expect(r).toEqual({ text: "éé", truncated: true });
	});
});
