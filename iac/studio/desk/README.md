# studio-desk

The studio desk's GitLab tool: an MCP server for your own Claude Code. Started in your studio brain, your desk agent can read what's going on in the studio's games, file issues from what you shaped together, comment, open doc MRs (the brief) and, when you say so, submit work to your cell.

It acts as your **desk account** (MetaVersig's `mv-agent`, Kannis's `kannis-desk-agent`), never as you. Your cell takes `agent-request` from your desk account as your own approval.

| Your desk agent can | Asks you first? |
|---|---|
| read: `games`, `status`, `issue`, `mr`, `search` | no |
| draft: `file_issue`, `edit_issue`, `comment`, `open_doc_mr` (only under `docs/`) | no |
| start or cancel work: `submit`, `stop` | **yes, every time** |

It can't merge, approve, delete, or touch a game's code. Merging is always your click.

Whatever the agent asks, the tool refuses:
- lines starting with `/` in anything it writes, because GitLab runs those as quick actions;
- `agent-*` and `vendor:*` labels outside submit and stop;
- files from outside the studio brain, or hidden ones;
- projects outside `studio/`.

## Install

You need Python 3.9 or newer and Claude Code, on Windows, macOS or Linux. Your desk token comes from MetaVersig.

1. Put `studio_desk.py` in `~/.config/studio-desk/` (on Windows: `C:\Users\<you>\.config\studio-desk\`).
2. In a terminal, go to your studio brain folder and run:
   ```
   python3 ~/.config/studio-desk/studio_desk.py setup
   ```
   On Windows: `python %USERPROFILE%\.config\studio-desk\studio_desk.py setup`.

   It does three things:
   - asks for your desk token and saves it in `~/.config/studio-desk/token`;
   - registers the tool with Claude Code for that folder only;
   - sets the permissions, so submit and stop always ask.
3. Start Claude Code in the studio brain.

`python3 studio_desk.py check` shows whose token it is and which games it reaches.

## Changes

This copy is published from homelab-infra (`iac/studio/desk`). Changes go there, by MR.
