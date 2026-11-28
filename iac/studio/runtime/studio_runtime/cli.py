"""studio: the agent's one way into the engine and, from step 2c-2c, GitLab.

Each verb is a request to a root-owned daemon over a local socket. The caller holds no token;
the daemon tells who it is from the kernel and acts on the worktree the caller stands in.
Results print as JSON on stdout, progress on stderr; the exit code is the verb's.
"""

import json
import os
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
  studio watch [--mr N]             the recorded play sessions of this game's builds
  studio watch <session>            what happened in one: its timeline, the numbers tuned
                                    on the Stage, the player log's warnings and errors,
                                    each with its time on the recording
  studio watch <session> --from 4:00 --to 4:20 [--frames N]
                                    a filmstrip of that stretch (a PNG path): N frames,
                                    evenly spaced, each stamped with its time
  studio watch <session> --at 4:10  one frame, full size (a PNG path)
  studio proto new <slug>           start a web prototype of one mechanic: prototypes/<issue>-<slug>/
                                    with a starter main.js (canvas, keys, knobs)
  studio proto build [<name>]       bundle it into one page (dist/index.html): your modules,
                                    three.js if you import it, images and sounds, all inside
  studio docs [--manual] <name>     Unity's docs for the pinned Editor, offline, as text
                                    (studio docs Mathf.Sin; studio docs --manual Prefabs);
                                    a partial name lists the matching pages

In a job (studio-agentd):
  studio done --summary <file>      finish: the cold gates on your committed HEAD, then the
                                    daemon pushes your branch and opens the MR with the file
                                    as its description; it refuses if the gates fail
  studio done --blocked <file>      stop: you can't complete the task; the file says why
  studio done --review <file>       a review job's notes, posted on the MR
  studio done --prototype <file>    a prototype job's finish: builds the page from your
                                    committed work; the daemon puts it on the portal and
                                    links it on the issue with the file as its note

For the person (from your own shell):
  studio jobs                       the cell's recent jobs
  studio attach <job> / detach <job>
                                    take over a running job's session, then hand it back
  studio vendor [list]              the AI vendors jobs may run, in your order: the first
                                    implements, the next reviews; a vendor:<name> label
                                    on an issue or MR picks one for that job
  studio vendor add <name> -- <command...>
                                    add any agent CLI (installed as agent): a job runs the
                                    command headless in its worktree, the prompt on stdin
                                    or wherever {prompt} appears; it must finish with
                                    `studio done` (the prompt says how). Also adds the
                                    label vendor:<name>
  studio vendor add claude|codex    use a built-in again after `remove`
  studio vendor resume <name> -- <command with {session}...>
                                    how an added vendor carries on a session handed back
  studio vendor remove <name>       stop using it (built-ins: until `add` again)
  studio vendor order <name>...     your preference, first to last (Claude first until
                                    you change it)

Run it inside a worktree (/srv/studio/<game>/work/<name>); `docs` works anywhere.
"""

LATER = {
    "map": "the project map arrives with the Studio Kit (step 3)",
    "play": "playing builds on the Forge arrives in step 3",
    "gen": "generation on the Forge arrives in step 4",
    "seat": "seats arrive in step 5",
}


def request(path, verb, args):
    """One request to a daemon: (exit code, result). Its progress lines go to stderr."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(path)
    except OSError as e:
        print(f"studio: can't reach {path}: {e}", file=sys.stderr)
        return 69, None
    common.send(s, {"verb": verb, "args": args})
    rc, result = 1, None
    for msg in common.read_lines(s):
        if "out" in msg:
            print(f"studio: {msg['out']}", file=sys.stderr, flush=True)
        if "rc" in msg:
            rc, result = msg["rc"], msg.get("result")
    return rc, result


def call(path, verb, args):
    rc, result = request(path, verb, args)
    if rc != 69:
        print(json.dumps(result, indent=2))
    return rc


def watch_main(args):
    """`studio watch`: the daemon lists sessions, fetches one and says what happened; a picture
    of a moment is cut here, as the caller, into the worktree's Logs/watch/."""
    from . import watch
    rest, look, it = [], {}, iter(args)
    for a in it:
        if a in ("--from", "--to", "--at", "--frames"):
            look[a[2:]] = next(it, "")
        else:
            rest.append(a)
    if not look:
        return call(common.AGENTD_SOCKET, "watch", rest)
    try:
        if "at" in look and ("from" in look or "to" in look):
            raise ValueError("--at is one frame; --from and --to are a filmstrip: use one or the other")
        if "at" not in look and not ("from" in look and "to" in look):
            raise ValueError("a filmstrip needs both --from and --to")
        t = {k: watch.parse_time(look[k]) for k in ("from", "to", "at") if k in look}
        frames = int(look.get("frames") or 8)
    except ValueError as e:
        print(json.dumps({"error": str(e)}, indent=2))
        return 2
    rc, res = request(common.AGENTD_SOCKET, "watch", rest)
    if rc != 0 or not isinstance(res, dict) or not res.get("recording"):
        if rc != 69:
            print(json.dumps(res if rc else {"error": (res or {}).get("no_recording") or "give a session's id"}, indent=2))
        return rc or 2
    out_dir = os.path.join(os.getcwd(), "Logs", "watch", res["session"]["id"])
    try:
        if "at" in t:
            pic = watch.frame(res["recording"], res.get("seconds"), t["at"], out_dir)
        else:
            pic = watch.filmstrip(res["recording"], res.get("seconds"), t["from"], t["to"], out_dir, frames)
    except (ValueError, RuntimeError, OSError) as e:
        print(json.dumps({"error": str(e)}, indent=2))
        return 1
    print(json.dumps({"session": res["session"]["id"], **pic}, indent=2))
    return 0


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
    if verb == "watch":
        return watch_main(args)
    if verb == "proto":
        # Local: the bundler runs here, as the caller, in the worktree.
        from . import proto
        return proto.main(args)
    if verb in ("done", "jobs", "attach", "detach", "vendor"):
        if verb == "done" and len(args) == 2:
            # Read here, inside the job: its /tmp is private to its unit, so the daemon couldn't.
            try:
                with open(args[1], encoding="utf-8", errors="replace") as f:
                    text = f.read(200000)
            except OSError as e:
                print(f"studio done: {e}", file=sys.stderr)
                return 2
            if args[0] == "--prototype":
                # The page is built here, as the job, from what's in the worktree; the daemon
                # then checks the work is committed and takes the built file.
                from . import proto
                try:
                    built = proto.build(proto.toplevel())
                except (ValueError, RuntimeError, OSError) as e:
                    print(json.dumps({"error": f"the prototype doesn't build: {e}"}, indent=2))
                    return 1
                print(f"studio: built {built['file']} ({built['bytes']} bytes)", file=sys.stderr, flush=True)
            args = [args[0], os.path.abspath(args[1]), text]
        return call(common.AGENTD_SOCKET, verb, args)
    return call(common.UNITYD_SOCKET, verb, args)


if __name__ == "__main__":
    sys.exit(main())
