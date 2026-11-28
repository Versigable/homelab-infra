"""Prototypes: a mechanic as a small web page, to try an idea before it's built in Unity
(vault notes/prototype-lane-plan).

A prototype is a directory in the game's repository, prototypes/<issue>-<slug>/, whose main.js
is its entry. `studio proto build` bundles it with esbuild into ONE file, dist/index.html, with
everything inside: the script (three.js too, when it's imported), the styles, and any image or
sound imported from JavaScript, as data. One file is what the portal can serve shut in a frame
that may load and call nothing. esbuild and three.js are on the cell, pinned (role studio_cell);
nothing is fetched at build time or at play time.

This runs as whoever calls `studio proto`: in a job, the agent, inside the job's sandbox.
studio-agentd takes the built file when the job says `studio done --prototype`.
"""

import html
import json
import os
import re
import shutil
import stat
import subprocess

WEB_KIT = "/opt/studio-web"
ESBUILD = os.path.join(WEB_KIT, "bin", "esbuild")
NODE_PATH = os.path.join(WEB_KIT, "node_modules")
KNOBS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "knobs.js")
DIR_RE = re.compile(r"^([1-9]\d{0,8})-([a-z0-9][a-z0-9-]{0,40})$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
MAX_BYTES = 15 << 20
# What `import x from './file.ext'` gives: a data: address for things a page shows or plays,
# the text for shaders and the like. Anything else esbuild refuses, by name.
LOADERS = {**{ext: "dataurl" for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".glb", ".gltf", ".bin",
                                         ".ogg", ".mp3", ".wav", ".woff2", ".ttf", ".hdr")},
           **{ext: "text" for ext in (".glsl", ".vert", ".frag", ".wgsl", ".txt", ".csv")}}

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>html,body{{margin:0;height:100%;background:#101418;color:#e6e6e6;font:14px system-ui,sans-serif;overflow:hidden}}
canvas#game{{display:block;width:100vw;height:100vh;outline:none}}</style>
<style>{css}</style>
</head><body><canvas id="game" tabindex="0"></canvas>
<script>{script}</script>
</body></html>
"""

STARTER = """// {title}
// A prototype: one mechanic, grey boxes, every number that changes the feel on a knob.
// The page gives you <canvas id="game"> filling the window. Build with `studio proto build`.
// For 3D: import * as THREE from 'three' (and 'three/addons/...'); it's bundled in.
import {{ knobs }} from 'studio-knobs';

const k = knobs({{ speed: [240, 0, 800], size: [40, 8, 160] }});

const canvas = document.getElementById('game');
const ctx = canvas.getContext('2d');
const keys = new Set();
addEventListener('keydown', (e) => {{ keys.add(e.code); if (e.code.startsWith('Arrow') || e.code === 'Space') e.preventDefault(); }});
addEventListener('keyup', (e) => keys.delete(e.code));

const box = {{ x: 200, y: 200 }};
let last = performance.now();

function frame(now) {{
  const dt = Math.min((now - last) / 1000, 0.05);
  last = now;
  if (canvas.width !== innerWidth || canvas.height !== innerHeight) {{ canvas.width = innerWidth; canvas.height = innerHeight; }}
  box.x += ((keys.has('ArrowRight') ? 1 : 0) - (keys.has('ArrowLeft') ? 1 : 0)) * k.speed * dt;
  box.y += ((keys.has('ArrowDown') ? 1 : 0) - (keys.has('ArrowUp') ? 1 : 0)) * k.speed * dt;
  ctx.fillStyle = '#101418';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#7cc4ff';
  ctx.fillRect(box.x - k.size / 2, box.y - k.size / 2, k.size, k.size);
  ctx.fillStyle = '#9aa4ae';
  ctx.fillText('Arrow keys move the box. Click here first.', 16, 24);
  requestAnimationFrame(frame);
}}
requestAnimationFrame(frame);
"""


def root(wt):
    return os.path.join(wt, "prototypes")


def dirs(wt, issue=None):
    """The worktree's prototype directories (names), optionally one issue's."""
    try:
        names = sorted(n for n in os.listdir(root(wt)) if DIR_RE.match(n) and os.path.isdir(os.path.join(root(wt), n)))
    except OSError:
        return []
    return [n for n in names if issue is None or int(DIR_RE.match(n).group(1)) == int(issue)]


def branch_issue(wt):
    """The issue a prototype job's worktree is for, from its branch (proto/<issue>), or None."""
    r = subprocess.run(["git", "-C", wt, "symbolic-ref", "--quiet", "--short", "HEAD"], capture_output=True, text=True, check=False)
    m = re.fullmatch(r"proto/([1-9]\d{0,8})", r.stdout.strip())
    return int(m.group(1)) if m else None


def pick(wt, name=None):
    """The one prototype directory meant: the named one, else this branch's issue's, else the
    only one. ValueError says what to do when it can't tell."""
    if name:
        name = os.path.basename(os.path.normpath(name))
        if not DIR_RE.match(name) or not os.path.isdir(os.path.join(root(wt), name)):
            raise ValueError(f"no prototype prototypes/{name} here (`studio proto new <slug>` makes one)")
        return name
    issue = branch_issue(wt)
    have = dirs(wt, issue)
    if len(have) == 1:
        return have[0]
    if not have:
        raise ValueError("no prototype here yet: `studio proto new <slug>` makes prototypes/<issue>-<slug>/")
    raise ValueError(f"which one? {', '.join(have)} (`studio proto build <name>`)")


def new(wt, slug, issue=None, title=None):
    """prototypes/<issue>-<slug>/ with a starter main.js. Returns its path from the worktree."""
    if not SLUG_RE.match(slug or ""):
        raise ValueError("the name is lower-case letters, digits and hyphens (studio proto new wall-jump)")
    issue = issue or branch_issue(wt)
    if not issue:
        raise ValueError("this isn't a prototype job's branch (proto/<issue>): give the issue, `studio proto new <slug> --issue N`")
    if dirs(wt, issue):
        raise ValueError(f"issue {issue} already has prototypes/{dirs(wt, issue)[0]}")
    d = os.path.join(root(wt), f"{int(issue)}-{slug}")
    os.makedirs(d)
    with open(os.path.join(d, "main.js"), "w", encoding="utf-8") as f:
        f.write(STARTER.format(title=(title or slug).replace("\n", " ")))
    with open(os.path.join(d, ".gitignore"), "w", encoding="utf-8") as f:
        f.write("# The built page: made by `studio proto build`, never committed.\ndist/\n")
    return os.path.relpath(d, wt)


def build(wt, name=None, title=None, run=subprocess.run):
    """Bundle the prototype into dist/index.html. Returns {"prototype", "file", "bytes"};
    RuntimeError carries esbuild's own words when the code doesn't bundle."""
    name = pick(wt, name)
    d = os.path.join(root(wt), name)
    if not os.path.isfile(os.path.join(d, "main.js")):
        raise ValueError(f"prototypes/{name}/main.js is the entry, and it isn't there")
    if not os.path.isfile(ESBUILD):
        raise RuntimeError(f"{ESBUILD} isn't on this cell (role studio_cell puts it there)")
    dist = os.path.join(d, "dist")
    shutil.rmtree(dist, ignore_errors=True)
    os.makedirs(dist)
    js = os.path.join(dist, "bundle.js")
    argv = [ESBUILD, "main.js", "--bundle", "--format=iife", "--minify", "--target=es2022", "--log-level=warning",
            "--log-limit=20", "--color=false", f"--alias:studio-knobs={KNOBS}", f"--outfile={js}"]
    argv += [f"--loader:{ext}={how}" for ext, how in sorted(LOADERS.items())]
    r = run(argv, cwd=d, capture_output=True, text=True, timeout=300, check=False,
            env={"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/tmp"), "NODE_PATH": NODE_PATH})
    if r.returncode != 0 or not os.path.isfile(js):
        raise RuntimeError((r.stderr or r.stdout or "esbuild failed").strip()[-3000:])
    with open(js, encoding="utf-8") as f:
        script = f.read()
    css = ""
    if os.path.isfile(os.path.join(d, "style.css")):
        with open(os.path.join(d, "style.css"), encoding="utf-8", errors="replace") as f:
            css = f.read(1 << 20)
    # Inside <script> and <style>, only the closing tag (and a comment's opening) can end them.
    script = re.sub(r"</(script)", r"<\\/\1", script, flags=re.I).replace("<!--", "<\\!--")
    css = re.sub(r"</(style)", r"<\\/\1", css, flags=re.I)
    out = os.path.join(dist, "index.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(PAGE.format(title=html.escape(title or name), css=css, script=script))
    os.unlink(js)
    size = os.path.getsize(out)
    if size > MAX_BYTES:
        raise RuntimeError(f"the built page is {size} bytes; a prototype is at most {MAX_BYTES} (smaller images and sounds)")
    return {"prototype": name, "file": os.path.relpath(out, wt), "bytes": size,
            "warnings": (r.stderr or "").strip()[-2000:] or None}


def take(wt, issue, dest, limit=MAX_BYTES):
    """For the daemon (root), at `studio done --prototype`: copy the issue's built page out of
    the job's worktree to dest. The worktree is the agent's, so nothing in it is followed on
    trust: each step of the path is opened without following links, and the page must be one
    regular file within the limit. Returns (directory name, bytes)."""
    have = dirs(wt, issue)
    if len(have) != 1:
        raise ValueError(f"issue {issue} needs exactly one prototypes/{issue}-<slug>/ directory (found {len(have)})")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(wt, flags | os.O_DIRECTORY)
    try:
        for part in ("prototypes", have[0], "dist"):
            try:
                nxt = os.open(part, flags | os.O_DIRECTORY, dir_fd=fd)
            except OSError as e:
                raise ValueError(f"prototypes/{have[0]}/dist isn't a plain directory ({e.strerror}); run `studio proto build`") from None
            os.close(fd)
            fd = nxt
        try:
            f = os.open("index.html", flags, dir_fd=fd)
        except OSError as e:
            raise ValueError(f"no built page ({e.strerror}); run `studio proto build`") from None
    finally:
        os.close(fd)
    with os.fdopen(f, "rb") as src:
        st = os.fstat(src.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise ValueError("the built page isn't a regular file")
        if st.st_size == 0 or st.st_size > limit:
            raise ValueError(f"the built page is {st.st_size} bytes; a prototype is 1 to {limit}")
        data = src.read(limit + 1)
    if len(data) > limit:
        raise ValueError("the built page grew while it was read")
    with open(dest, "wb") as out:
        out.write(data)
    os.chmod(dest, 0o600)
    return have[0], len(data)


def toplevel():
    """The worktree the caller stands in, or "" outside one."""
    return subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                          check=False).stdout.strip()


def main(args, wt=None):
    """`studio proto new|build`, in a worktree. Prints JSON; returns the exit code."""
    wt = wt or toplevel()
    try:
        if not wt:
            raise ValueError("run it inside the game's worktree")
        if args[:1] == ["new"] and len(args) in (2, 4) and (len(args) == 2 or args[2] == "--issue"):
            path = new(wt, args[1], issue=int(args[3]) if len(args) == 4 else None)
            res = {"made": path, "next": f"write {path}/main.js, then `studio proto build`"}
        elif args[:1] == ["build"] and len(args) <= 2:
            res = build(wt, args[1] if len(args) == 2 else None)
        else:
            raise ValueError("usage: studio proto new <slug> [--issue N] | studio proto build [<name>]")
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as e:
        print(json.dumps({"error": str(e)}, indent=2))
        return 1
    print(json.dumps(res, indent=2))
    return 0
