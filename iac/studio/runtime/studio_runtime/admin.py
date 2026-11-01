"""studio-admin: root-only housekeeping for a cell's games (studio-agentd uses the same code).

  studio-admin workspace <game> <name>   fetch the game into its mirror and make a worktree
                                         for agent at /srv/studio/<game>/work/<name>
  studio-admin upload <game> <revision>  put a revision's gated Linux build, already on this
                                         cell, in the hub's depot (with its MR, if GitLab
                                         knows one); the portal shows it from then on

The mirror is fetched as root with the agent account's token, through Squid's default class,
and the token reaches git only through an askpass helper: never on a command line, where any
local user could read it.
"""

import json
import os
import shutil
import subprocess
import sys

from . import common

RUNTIME_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASKPASS = os.path.join(RUNTIME_DIR, "bin", "studio-git-askpass")


def git_root(cfg, args, cwd=None):
    env = {"HOME": "/root", "PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
           "GIT_ASKPASS": ASKPASS, "GIT_TERMINAL_PROMPT": "0", "STUDIO_GITLAB_USER": cfg["gitlab_agent"],
           "https_proxy": cfg["root_proxy"], "http_proxy": cfg["root_proxy"]}
    r = subprocess.run(["git", "-c", "credential.helper=", *args], cwd=cwd, env=env,
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {r.stderr.strip()[-600:]}")
    return r.stdout.strip()


def game_dirs(cfg, game):
    """/srv/studio/<game> and work/: root-owned, readable by the work group."""
    for d in (os.path.join(common.STUDIO_ROOT, game), os.path.join(common.STUDIO_ROOT, game, "work")):
        os.makedirs(d, exist_ok=True)
        shutil.chown(d, "root", cfg["work_group"])
        os.chmod(d, 0o755)


def mirror(cfg, game):
    """A bare mirror of studio/<game>, root's, group-readable. Fetched fresh each time."""
    path = os.path.join(common.STUDIO_ROOT, game, "mirror.git")
    url = f"{cfg['gitlab_url']}/{cfg['gitlab_group']}/{game}.git"
    if not os.path.isdir(path):
        git_root(cfg, ["clone", "--quiet", "--mirror", url, path])
    else:
        git_root(cfg, ["remote", "set-url", "origin", url], cwd=path)
        git_root(cfg, ["fetch", "--quiet", "--prune", "origin"], cwd=path)
    subprocess.run(["chgrp", "-R", cfg["work_group"], path], check=True)
    subprocess.run(["chmod", "-R", "g+rX,o-rwx", path], check=True)
    return path


def workspace(cfg, game, name, branch=None):
    wt = common.Worktree(game, name)
    game_dirs(cfg, game)
    src = mirror(cfg, game)
    if os.path.exists(wt.path):
        raise RuntimeError(f"{wt.path} already exists")
    git_root(cfg, ["clone", "--quiet", "--no-hardlinks", src, wt.path])
    if branch:
        # Continue the branch if GitLab has it (a fix on an open MR); otherwise start it here.
        existing = subprocess.run(["git", "-C", wt.path, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}"],
                                  capture_output=True, check=False).returncode == 0
        git_root(cfg, ["checkout", "--quiet", "-B", branch] + ([f"origin/{branch}"] if existing else []), cwd=wt.path)
    agent = cfg["agent_user"]
    for k, v in (("user.name", cfg["gitlab_agent"]), ("user.email", f"{cfg['gitlab_agent']}@studio.ninjaprivacy.org")):
        git_root(cfg, ["config", k, v], cwd=wt.path)
    # agent owns it; the work group (the warm editor, as unity) can write too, now and later.
    subprocess.run(["chown", "-R", f"{agent}:{cfg['work_group']}", wt.path], check=True)
    subprocess.run(["find", wt.path, "-type", "d", "-exec", "chmod", "g+rwxs", "{}", "+"], check=True)
    subprocess.run(["chmod", "-R", "g+rwX,o-rwx", wt.path], check=True)
    subprocess.run(["setfacl", "-R", "-m", f"g:{cfg['work_group']}:rwX", wt.path], check=True)
    subprocess.run(["find", wt.path, "-type", "d", "-exec", "setfacl", "-d", "-m",
                    f"g:{cfg['work_group']}:rwX,u::rwX,g::rwX,o::-", "{}", "+"], check=True)
    return wt.path


def upload(cfg, game, rev):
    """A gate result recorded by studio-unityd (/var/lib/studio/results/<game>/<rev>), its build
    into the depot, and the manifest. rev may be a unique prefix."""
    from . import hub as hubmod
    from .gitlab import GitLab
    if not common.NAME_RE.match(game):
        raise ValueError("bad game name")
    base = os.path.join(common.STATE_DIR, "results", game)
    revs = [r for r in (os.listdir(base) if os.path.isdir(base) else []) if r.startswith(rev)]
    if len(revs) != 1:
        raise ValueError(f"{len(revs)} gate results match {rev!r} in {base}")
    with open(os.path.join(base, revs[0], "gate-full.json"), encoding="utf-8") as f:
        gate = json.load(f)
    hub = hubmod.Hub(cfg)
    dep = hubmod.deposit(hub, game, gate)
    gl = GitLab(cfg)
    pid = gl.project(GitLab.pid(f"{cfg['gitlab_group']}/{game}"))["id"]
    mrs = gl.call("GET", f"/projects/{pid}/repository/commits/{gate['revision']}/merge_requests") or []
    res = hub.call("POST", "/v1/builds", hubmod.manifest(game, gate, dep, mr=mrs[0] if mrs else None))
    return {**res, "object": dep["object"], "mr": mrs[0]["web_url"] if mrs else None}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if os.geteuid() != 0:
        print("studio-admin: root only", file=sys.stderr)
        return 13
    cfg = common.load_config()
    if len(argv) in (3, 4) and argv[0] == "workspace":
        print(workspace(cfg, argv[1], argv[2], argv[3] if len(argv) == 4 else None))
        return 0
    if len(argv) == 3 and argv[0] == "upload":
        print(json.dumps(upload(cfg, argv[1], argv[2]), indent=2))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
