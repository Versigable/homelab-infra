"""studio-admin: root-only housekeeping for a cell's games (studio-agentd uses the same code).

  studio-admin workspace <game> <name>   fetch the game into its mirror and make a worktree
                                         for agent at /srv/studio/<game>/work/<name>
  studio-admin upload <game> <revision>  put a revision's gated Linux build, already on this
                                         cell, in the hub's depot (with its MR, if GitLab
                                         knows one); the portal shows it from then on
  studio-admin republish <job>           publish a failed job's kept work: its write-back
                                         failed after the gates passed (push, MR, ledger,
                                         depot, review), with no new job
  studio-admin game seed <game>          open an MR on studio/<game> (as the agent account)
                                         whose tree is studio/template's main: a new game's
                                         start. A person makes the empty project first
  studio-admin game kit <game>           open an MR on studio/<game> that brings its Studio
                                         Kit (Packages/com.studio.kit) to studio/template's
                                         main; nothing else changes. Games never edit the kit

The mirror is fetched as root with the agent account's token, through Squid's default class,
and the token reaches git only through an askpass helper: never on a command line, where any
local user could read it.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

from . import common

RUNTIME_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASKPASS = os.path.join(RUNTIME_DIR, "bin", "studio-git-askpass")
TEMPLATE = "template"                    # studio/template: new games start from it; the kit's home
KIT_DIR = "Packages/com.studio.kit"
LFS_HEADER = "version https://git-lfs.github.com/spec/"


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
    from .gitlab import GitLab, GitLabError
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
    # A revision that was never pushed (a pair session's local commit) has no MR: GitLab
    # doesn't know the commit at all and answers 404.
    try:
        mrs = gl.call("GET", f"/projects/{pid}/repository/commits/{gate['revision']}/merge_requests") or []
    except GitLabError as e:
        if e.status != 404:
            raise
        mrs = []
    res = hub.call("POST", "/v1/builds", hubmod.manifest(game, gate, dep, mr=mrs[0] if mrs else None))
    return {**res, "object": dep["object"], "mr": mrs[0]["web_url"] if mrs else None}


def republish(cfg, jid):
    """A failed implement job whose gates passed and whose worktree studio-agentd kept: its
    write-back again, through studio-agentd's own code. studio-agentd leaves failed jobs alone,
    so this doesn't race it; the review it queues runs at the daemon's next poll."""
    from .agentd import FAILED, Agentd, Job
    job = Job(jid)
    res = job.get("result") or {}
    if job["state"] != "failed" or job["kind"] != "implement" or res.get("kind") != "summary" or not job.get("ledger"):
        raise ValueError(f"{jid} isn't a failed implement job with gated work (state {job['state']})")
    if not os.path.isdir(job.get("worktree") or ""):
        raise ValueError(f"{jid}'s worktree {job.get('worktree')} is gone")
    d = Agentd(cfg)
    item = job["item"]
    d.gl.relabel(item["kind"], item["project_id"], item["iid"], remove=[FAILED])
    try:
        d.publish(job, res)
    except Exception as e:
        d.fail(job, f"republish failed: {e}")
        raise
    d.cleanup(job)
    return {"job": jid, "state": job["state"], "mr": job.get("mr_url"), "revision": job.get("pushed")}


def from_template(cfg, what, game, gl=None):
    """studio-admin game seed|kit <game>: the template's tree (seed) or its Studio Kit (kit) on
    a branch of studio/<game>, committed and pushed as the agent account, and an MR for a
    person to merge. Idempotent: an open MR from the same template revision is just named."""
    from .gitlab import GitLab
    if what not in ("seed", "kit"):
        raise ValueError("game seed|kit")
    if not common.NAME_RE.match(game):
        raise ValueError("bad game name")
    if game == TEMPLATE:
        raise ValueError("the template is where kit changes are made; it isn't seeded from itself")
    gl = gl or GitLab(cfg)
    group = cfg["gitlab_group"]
    proj = gl.project(GitLab.pid(f"{group}/{game}"))
    tproj = gl.project(GitLab.pid(f"{group}/{TEMPLATE}"))
    default, tdefault = proj.get("default_branch") or "main", tproj.get("default_branch") or "main"
    game_dirs(cfg, TEMPLATE)
    game_dirs(cfg, game)
    tpl, src = mirror(cfg, TEMPLATE), mirror(cfg, game)
    work = tempfile.mkdtemp(prefix=f"{what}-", dir=os.path.join(common.STUDIO_ROOT, game))
    try:
        git_root(cfg, ["clone", "--quiet", "--no-hardlinks", src, work])
        git_root(cfg, ["fetch", "--quiet", "--no-tags", tpl, f"+refs/heads/{tdefault}:refs/studio/template"], cwd=work)
        tsha = git_root(cfg, ["rev-parse", "refs/studio/template"], cwd=work)
        paths = ["."] if what == "seed" else [KIT_DIR]
        if what == "kit" and not git_root(cfg, ["ls-tree", "-d", "refs/studio/template", KIT_DIR], cwd=work):
            raise ValueError(f"studio/template@{tsha[:10]} has no {KIT_DIR} yet")
        lfs = subprocess.run(["git", "-C", work, "grep", "-l", "-F", LFS_HEADER, "refs/studio/template", "--", *paths],
                             capture_output=True, text=True, check=False).stdout.strip()
        if lfs:
            raise ValueError("the template has Git LFS files, which this copy doesn't carry yet: "
                             + ", ".join(p.split(":", 1)[-1] for p in lfs.splitlines()[:5]))
        branch = f"studio/{what}-{tsha[:10]}"
        mr = gl.mr_for_branch(proj["id"], branch)
        if mr:
            return {"game": game, "template": tsha, "mr": mr["web_url"], "note": "already open"}
        git_root(cfg, ["checkout", "--quiet", "-B", branch, f"origin/{default}"], cwd=work)
        git_root(cfg, ["rm", "-r", "-q", "--ignore-unmatch", "--", *paths], cwd=work)
        git_root(cfg, ["checkout", "refs/studio/template", "--", *paths], cwd=work)
        if not git_root(cfg, ["status", "--porcelain"], cwd=work):
            return {"game": game, "template": tsha, "mr": None, "note": f"{default} already matches the template"}
        agent = cfg["gitlab_agent"]
        if what == "seed":
            title = f"Seed from studio/template@{tsha[:10]}"
            body = (f"This game starts from the studio template at `{tsha[:10]}` ({group}/{TEMPLATE}'s {tdefault}): "
                    "every file here is the template's, the Studio Kit with them. The template's changes passed "
                    "the gates before they merged; this MR isn't gated again here.\n\nMerge it, and the game's "
                    "issues build on it.")
        else:
            title = f"Studio Kit from studio/template@{tsha[:10]}"
            body = (f"The Studio Kit (`{KIT_DIR}`) as it is in {group}/{TEMPLATE} at `{tsha[:10]}`. Nothing else "
                    "changes: games never edit the kit, so this replaces the folder as a whole.\n\nThis command "
                    "doesn't gate it. Before you merge, approve the MR (`agent-request`) for a review on your cell.")
        body += f"\n\nOpened by `studio-admin game {what} {game}` on cell-{cfg['owner']}."
        git_root(cfg, ["-c", f"user.name={agent}", "-c", f"user.email={agent}@studio.ninjaprivacy.org",
                       "commit", "--quiet", "-m", f"{title}\n\n{body}"], cwd=work)
        url = f"{cfg['gitlab_url']}/{group}/{game}.git"
        git_root(cfg, ["push", "--quiet", url, f"HEAD:refs/heads/{branch}"], cwd=work)
        mr = gl.open_mr(proj["id"], branch, default, title, body, [])
        return {"game": game, "template": tsha, "branch": branch, "mr": mr["web_url"]}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if os.geteuid() != 0:
        print("studio-admin: root only", file=sys.stderr)
        return 13
    cfg = common.load_config()
    if len(argv) == 3 and argv[0] == "game" and argv[1] in ("seed", "kit"):
        print(json.dumps(from_template(cfg, argv[1], argv[2]), indent=2))
        return 0
    if len(argv) in (3, 4) and argv[0] == "workspace":
        print(workspace(cfg, argv[1], argv[2], argv[3] if len(argv) == 4 else None))
        return 0
    if len(argv) == 3 and argv[0] == "upload":
        print(json.dumps(upload(cfg, argv[1], argv[2]), indent=2))
        return 0
    if len(argv) == 2 and argv[0] == "republish":
        print(json.dumps(republish(cfg, argv[1]), indent=2))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
