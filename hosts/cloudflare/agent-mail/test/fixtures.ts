// Test-only tokens. Shared by vitest.config.ts (which hashes them into the
// AGENT_TOKEN_HASHES binding) and the tests (which present them).
export const TOKENS = {
	"mv-agent": "agentmail_test_mv_0123456789abcdefghijklmnopqrstuv",
	merquery: "agentmail_test_mq_0123456789abcdefghijklmnopqrstuv",
} as const;
export const DOMAIN = "agents.ninjaprivacy.org";
