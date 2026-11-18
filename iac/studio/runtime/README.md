# Studio runtime (a cell's daemons, the `studio` CLI, and the hub's daemon)

Deployed to `/opt/studio/runtime` by roles `studio_cell` and `studio_hub`; design in the vault
at `1-Projects/game-studio-platform/notes/step-2c-2-plan` (cell) and `step-2b-1-plan` (hub).
Python 3.13, standard library only.

| Program | Runs as | Does |
|---|---|---|
| `studio-unityd` | root (service, cell) | The Unity broker: warm editors per worktree (as `unity`), Pipeline commands through a deny list, cold gates and builds on committed revisions, one at a time |
| `studio-agentd` | root (service, cell) | Approved GitLab work → sandboxed jobs as `agent` → MRs with a ledger; puts each gated build in the hub's depot. Runs the AI vendors the person has (`studio vendor`, `vendors.py`) |
| `studio` | `agent` | The agent's CLI: one request per call to the daemons' sockets in `/run/studio` |
| `studio-brain-sync` | root (service, cell) | The studio brain, read-only, as files in `/srv/studio/_brain` for the cell's agents: the cell's reader account on the brain's CouchDB, the people's notes without the desk agents' memory or the archive (`--check`: it reads, can't write, the infra brains refuse it) |
| `studio-slackd` | root (service, cell) | The person's desk in the studio's Slack, as their bot (Socket Mode, outbound only): takes requests only from the person's Slack ID; runs each turn as `desk`; serves the desk's GitLab tools on `/run/studio/desk.sock` (`desk`, inside a turn, only); submits and stops work only on the person's button, as the desk account; keeps the brain working copy (`deskbrain.py`, written back as the person's desk account); posts the cell's work from studio-agentd's events (`/var/lib/studio/events.jsonl`) to the builds channel, one message per piece of work that always shows where it stands (`feed.py`), DMs what needs the person, and a morning list of what's waiting on them; files a huddle in the brain when Slack's AI notes for it arrive, on the desk of the person who started it (`huddles.py`) |
| `bin/studio-desk-turn` | `desk`, in a turn's unit | One Slack desk turn: Claude with the desk's settings, tools and rules, resuming the thread's session |
| `bin/studio-desk-mcp` | `desk`, started by Claude | The desk's tools for Claude: a stdio MCP server that forwards each call to studio-slackd's socket |
| `bin/studio-job-run`, `bin/studio-job-exec` | `agent`, in a job's unit | A job's process: Claude or Codex with the studio's flags, or a vendor the person added (`studio vendor add`) with its own command |
| `studio-admin` | root (cell) | Game mirrors and worktrees (`workspace <game> <name>`), `upload <game> <rev>` of a gated build to the depot, and MRs from `studio/template` as the agent account: `game seed <game>` (a new game's tree) and `game kit <game>` (its Studio Kit); studio-agentd reuses it |
| `bin/studio-editor-run` | `unity`, in a warm editor's unit | A private X server (auth cookie, no TCP, no abstract socket), then the Editor on it |
| `bin/studio-git-askpass` | root, for git | The agent account's token from `/etc/studio/gitlab-token`, never on a command line |
| `studio-hubd` | `studio-hub` (service, hub) | The depot (objects by sha256, build manifests with each build's Stage catalogue) and the broker's play sessions for cells and the Forge over mutual TLS; the portal (Play, the Stage, one asset on the Stage), and Traefik's stream check, behind Traefik and Authentik |
| `forge-playd` | root (service, Forge) | Syncs with the broker every 2 s: runs each session's build (fetched by hash, unpacked once) in a locked play container on its slot, opened with `-studio-open`, reports ready and viewers, keeps ended sessions' player logs and hands their Stage tweaks to the hub |

The cell's daemons take the caller from the kernel (`SO_PEERCRED`, then its cgroup:
`studio-job-<id>.service` is the job, `studio-desk-turn-<n>.service` the Slack desk's turn) and
act only on the worktree the caller stands in. The
hub takes the caller from its client certificate (signed by the hub's CA) and the address it
connects from.
