// agent-mail: receive-only inboxes for homelab agents. See README.md.
import { handleFetch } from "./api";
import { handleEmail } from "./inbound";

export default {
	email: (message, env) => handleEmail(message, env),
	fetch: (request, env) => handleFetch(request, env),
} satisfies ExportedHandler<Env>;
