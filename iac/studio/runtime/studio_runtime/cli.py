"""studio: the agent's one way into the engine and, from step 2c-2c, GitLab.

Each verb is a request to a root-owned daemon over a local socket. The caller holds no token;
the daemon tells who it is from the kernel and acts on the worktree the caller stands in.
Results print as JSON on stdout, progress on stderr; the exit code is the verb's.
"""

import json
import socket
import sys

from . import common

USAGE = """usage: studio <verb> [args]

  studio status                     warm editors and the gate lock
  studio start | stop               start or stop this worktree's warm editor
  studio compile                    recompile on the warm editor; errors as JSON
  studio test [--mode editor|playmode|all] [--filter NAME]
                                    tests on the warm editor (compiles first)
  studio unity <group> <cmd> [--param value ...]
                                    a Pipeline command on the warm editor
                                    (list them: studio unity editor search ...)
  studio capture [--width N] [--height N]
                                    a camera capture from the warm editor (a PNG path)
  studio gate [quick|full]          cold gates on the committed HEAD: quick = EditMode
                                    tests; full = + PlayMode tests + a Linux build
  studio build linux|windows        a cold build of the committed HEAD
  studio docs [--manual] <name>     Unity's docs for the pinned Editor, offline, as text
                                    (studio docs Mathf.Sin; studio docs --manual Prefabs);
                                    a partial name lists the matching pages

Run it inside a worktree (/srv/studio/<game>/work/<name>); `docs` works anywhere.
"""

LATER = {
    "ledger": "the evidence ledger arrives with studio-agentd (step 2c-2c)",
    "done": "`studio done` arrives with studio-agentd (step 2c-2c)",
    "attach": "`studio attach` arrives with studio-agentd (step 2c-2c)",
    "map": "the project map arrives with the Studio Kit (step 3)",
    "play": "playing builds on the Forge arrives in step 3",
    "gen": "generation on the Forge arrives in step 4",
    "seat": "seats arrive in step 5",
}


def call(path, verb, args):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(path)
    except OSError as e:
        print(f"studio: can't reach {path}: {e}", file=sys.stderr)
        return 69
    common.send(s, {"verb": verb, "args": args})
    rc = 1
    for msg in common.read_lines(s):
        if "out" in msg:
            print(f"studio: {msg['out']}", file=sys.stderr, flush=True)
        if "rc" in msg:
            rc = msg["rc"]
            print(json.dumps(msg.get("result"), indent=2))
    return rc


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    verb, args = argv[0], argv[1:]
    if verb in LATER:
        print(f"studio: {LATER[verb]}", file=sys.stderr)
        return 2
    if verb == "docs":
        # Local and read-only: the pages are on disk, readable by everyone; no daemon involved.
        from . import docs
        return docs.main(args)
    return call(common.UNITYD_SOCKET, verb, args)


if __name__ == "__main__":
    sys.exit(main())
