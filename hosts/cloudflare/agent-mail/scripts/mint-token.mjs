#!/usr/bin/env node
// Mint one agent's read token without showing it to anyone.
//
//   node scripts/mint-token.mjs --agent mv-agent --out ~/.claude/secrets/api-keys.env [--var AGENT_MAIL_TOKEN]
//
// Appends VAR=<token> to --out (creating it, mode 600 where the OS honours it) and
// prints only {"agent","sha256"}. The hash goes into the Worker's
// AGENT_TOKEN_HASHES secret; the token never leaves the file. Refuses to add a
// second line for a VAR that is already there: delete the old line yourself to rotate.

import { createHash, randomBytes } from "node:crypto";
import { appendFileSync, chmodSync, existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";

function arg(name, fallback) {
	const i = process.argv.indexOf(`--${name}`);
	return i > 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

const agent = arg("agent");
const outArg = arg("out");
const varName = arg("var", "AGENT_MAIL_TOKEN");

if (!agent || !/^[a-z0-9][a-z0-9._-]{0,63}$/.test(agent) || !outArg) {
	console.error("usage: mint-token.mjs --agent <name> --out <env file> [--var NAME]");
	process.exit(2);
}
if (!/^[A-Z_][A-Z0-9_]*$/.test(varName)) {
	console.error("--var must be an upper-case env var name");
	process.exit(2);
}
const out = outArg.replace(/^~(?=$|[\\/])/, homedir());

const existing = existsSync(out) ? readFileSync(out, "utf8") : "";
if (new RegExp(`^${varName}=`, "m").test(existing)) {
	console.error(`${varName} is already set in ${out}; remove that line first to rotate`);
	process.exit(1);
}

const token = `agentmail_${randomBytes(32).toString("base64url")}`;
const sep = existing === "" || existing.endsWith("\n") ? "" : "\n";
appendFileSync(out, `${sep}${varName}=${token}\n`, { mode: 0o600 });
try {
	chmodSync(out, 0o600);
} catch {
	// Windows/NTFS: the directory ACL governs instead.
}
console.log(JSON.stringify({ agent, sha256: createHash("sha256").update(token).digest("hex") }));
