# Studio runtime (a cell's daemons and the `studio` CLI)

Deployed to `/opt/studio/runtime` by role `studio_cell`; design in the vault at
`1-Projects/game-studio-platform/notes/step-2c-2-plan`. Python 3.13, standard library only.

| Program | Runs as | Does |
|---|---|---|
| `studio-unityd` | root (service) | The Unity broker: warm editors per worktree (as `unity`), Pipeline commands through a deny list, cold gates and builds on committed revisions, one at a time |
| `studio` | `agent` | The agent's CLI: one request per call to the daemons' sockets in `/run/studio` |
| `studio-admin` | root | Game mirrors and worktrees (`workspace <game> <name>`); studio-agentd reuses it |
| `bin/studio-editor-run` | `unity`, in a warm editor's unit | A private X server (auth cookie, no TCP, no abstract socket), then the Editor on it |
| `bin/studio-git-askpass` | root, for git | The agent account's token from `/etc/studio/gitlab-token`, never on a command line |

The daemons take the caller from the kernel (`SO_PEERCRED`, then its cgroup:
`studio-job-<id>.service` is the job) and act only on the worktree the caller stands in.
