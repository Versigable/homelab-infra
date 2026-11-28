"""An item's "Read first" list: the studio-brain notes its person says the work depends on
(the studio brain's Studio/writing-issues, "Read first"; vault notes/prototype-lane-plan).

studio-agentd checks the list against the cell's read-only copy of the brain when it claims
an item, and doesn't start a job whose list names a note that isn't there. The notes it found
go to the writer and the reviewer alike, and the result records how each note stood.

Everything here reads: the item's text, and files under the brain copy. A reference is
written by a person (or their desk), so it is only ever looked up inside the copy."""
import datetime as dt
import difflib
import hashlib
import os
import re
import time
import urllib.parse

MAX_NOTES = 30                      # a longer list isn't "read first" any more
MAX_REF = 300                       # characters of one reference
MAX_HEADING_SCAN = 4 << 20          # a note bigger than this isn't searched for a heading
MAX_HASH = 64 << 20
NOT_COPIED = ("memory", "archive")  # the top folders studio-brain-sync leaves out

COMMENT_RE = re.compile(r"(?m)^--- comment by (\S+) at [^\n]*$")
HTML_COMMENT_RE = re.compile(r"(?s)<!--.*?-->")
FENCE_RE = re.compile(r"^\s{0,3}(```|~~~)")
HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
RULE_RE = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$")
LIST_RE = re.compile(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+(?:\[[ xX]\]\s+)?(.*)$")
TITLE_RE = re.compile(r"^read first\s*(?:\([^)]*\))?\s*:?$")
NOTHING = ("", "none", "none.", "n/a", "na", "nothing", "-", "—", "–")
WIKI_RE = re.compile(r"!?\[\[([^\[\]\n]+)\]\]")
TICK_RE = re.compile(r"`([^`\n]+)`")
LINK_RE = re.compile(r"\[[^\]\n]*\]\(([^)\s]+)\)")
SEPARATORS = (" — ", " – ", " - ", ": ", " (", ", ")


def people_text(snapshot, skip=()):
    """The parts of an item's snapshot (studio-agentd's: the description, then each comment
    under a `--- comment by <name> at <time>` line) that people wrote: every part but the
    comments of the accounts in `skip`. A cell's own notes quote lists and name notes; they
    never add to what must be read."""
    parts = COMMENT_RE.split(snapshot or "")
    out = [parts[0]]
    for who, body in zip(parts[1::2], parts[2::2]):
        if who not in skip:
            out.append(body)
    return out


def plain(s):
    """A line without its emphasis marks, for comparing."""
    return re.sub(r"\s+", " ", re.sub(r"[*_`]", "", s)).strip()


def reference(s, anywhere):
    """The reference a line holds, as (path, heading, loose), or None if it holds none. A wiki
    link, a `code span` or a Markdown link is taken as written; so is a bare path. `loose`
    marks a bare reference, where the words after the path might be the heading or a remark.
    `anywhere` (a list item) finds a marked reference anywhere on the line; otherwise the
    line has to start with one."""
    s = s.strip().lstrip("*_").strip()
    for rx, wiki in ((WIKI_RE, True), (TICK_RE, False), (LINK_RE, False)):
        m = rx.search(s) if anywhere else rx.match(s)
        if m:
            target = m.group(1).split("|")[0] if wiki else urllib.parse.unquote(m.group(1))
            path, _, heading = target.partition("#")
            return path.strip(), heading.strip() or None, False
    words = s.split()
    if not words:
        return None
    if re.match(r"(?i)^https?://", s):
        return words[0], None, False
    # A bare reference: the line's first word has to look like a path. A list item of one
    # word is taken as a note's name.
    if not ("/" in words[0].split("#")[0] or ".md" in words[0] or (anywhere and len(words) == 1)):
        return None
    before, mark, after = s.partition("#")
    if ".md" in before:                     # the path runs to its `.md`, spaces and all
        cut = before.index(".md") + 3
        rest = s[cut:]
        if rest.startswith("#"):
            return s[:cut].strip(), rest[1:].strip() or None, True
        return (s[:cut].strip(), None, False) if not rest or not (rest[0].isalnum() or rest[0] in "/_-") else None
    if mark:
        return before.strip(), after.strip() or None, True
    return words[0].rstrip(".,;:"), None, False


def listed(text):
    """What stands under every "Read first" title in a piece of Markdown: (references, lines
    that couldn't be read as one). A title is a heading, or a line of its own, that says
    "Read first". Under it, each list item is one reference; other lines are prose, unless
    they start with a reference. A heading's list runs to the next heading or rule; a plain
    title's list ends at the first blank line after it."""
    refs, bad = [], []
    fenced, inside, got = False, None, 0
    for line in HTML_COMMENT_RE.sub("", text or "").splitlines():
        if FENCE_RE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        h = HEADING_RE.match(line)
        if h or (not LIST_RE.match(line) and TITLE_RE.match(plain(line).casefold())):
            title = plain(h.group(2) if h else line).casefold()
            inside, got = ("heading" if h else "plain") if TITLE_RE.match(title) else None, 0
            continue
        if not inside:
            continue
        if RULE_RE.match(line) or (inside == "plain" and got and not line.strip()):
            inside = None
            continue
        if not line.strip():
            continue
        item = LIST_RE.match(line)
        content = item.group(1) if item else line
        if item and plain(content).casefold() in NOTHING:
            continue
        ref = reference(content, anywhere=bool(item))
        if ref:
            refs.append(ref)
            got += 1
        elif item:
            bad.append(line.strip()[:200])
            got += 1
        elif inside == "plain" and got:
            inside = None
    return refs, bad


def files(root):
    """Every file in the brain copy, as a path from its top folder. Links aren't files of the
    copy (studio-brain-sync writes none), so one is never matched or offered."""
    out = []
    top = os.path.realpath(root)
    for base, dirs, names in os.walk(top):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        rel = os.path.relpath(base, top)
        for n in sorted(names):
            if not n.startswith(".") and not os.path.islink(os.path.join(base, n)):
                out.append(n if rel == "." else f"{rel.replace(os.sep, '/')}/{n}")
    return out


def headings(text):
    """A note's headings, in order, outside its front matter and code blocks."""
    out, fenced = [], False
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        end = next((i for i, ln in enumerate(lines[1:], 1) if ln.strip() == "---"), 0)
        lines = lines[end + 1:]
    for line in lines:
        if FENCE_RE.match(line):
            fenced = not fenced
        elif not fenced:
            m = HEADING_RE.match(line)
            if m and plain(m.group(2)):
                out.append(plain(m.group(2)))
    return out


def heading_tries(heading, loose):
    """What a reference's heading might be, longest first: its words in full, and for a loose
    reference those words cut at each place a remark could begin."""
    tries = {heading}
    if loose:
        for sep in SEPARATORS:
            start = 0
            while (i := heading.find(sep, start)) > 0:
                tries.add(heading[:i].strip())
                start = i + 1
    return sorted(tries - {""}, key=len, reverse=True)


def find_heading(text, tries):
    """The heading a reference names, as the note writes it, or None. `A#B` is B under A.
    A `^block` reference is found by its mark."""
    have = headings(text)
    folded = [h.casefold() for h in have]
    for t in tries:
        if t.startswith("^"):
            if re.fullmatch(r"\^[A-Za-z0-9-]{1,64}", t) and re.search(r"(?m)(?:^|\s)" + re.escape(t) + r"\s*$", text):
                return t
            continue
        at, found = 0, []
        for part in (plain(p).casefold() for p in t.split("#")):
            if not part or part not in folded[at:]:
                found = None
                break
            at = folded.index(part, at) + 1
            found.append(have[at - 1])
        if found:
            return "#".join(found)
    return None


def _inside(top, rel):
    """The real path of `rel` under `top`, if it stays inside it."""
    real = os.path.realpath(os.path.join(top, *rel.split("/")))
    return real if real.startswith(top + os.sep) else None


def resolve(root, ref, known=None):
    """One reference against the brain copy: a note {"path", "heading", "edited", "sha"}, or
    {"ref", "problem", "wait"} saying what's wrong. `wait` is whether a copy that's a moment
    behind the person's edit could be the reason."""
    raw_path, heading, loose = ref
    shown = raw_path + (f"#{heading}" if heading else "")
    bad = lambda why, wait=False: {"ref": shown[:MAX_REF], "problem": why, "wait": wait}  # noqa: E731
    top = os.path.realpath(root)
    p = raw_path.strip().replace("\\", "/")
    for prefix in (root.rstrip("/") + "/", top + "/", "$STUDIO_BRAIN/", "./"):
        if p.startswith(prefix):
            p = p[len(prefix):]
    p = p.rstrip("/") if p.rstrip("/") else p
    parts = p.split("/")
    if len(shown) > MAX_REF or re.search(r"[\x00-\x1f]", shown):
        return bad("isn't a path")
    if not p or p.startswith("/") or re.match(r"^[A-Za-z]:", p) or any(x in ("", ".", "..") for x in parts):
        return bad("isn't a path from the brain's top folder (like `Games/sandbox/jump.md`)")
    if parts[0].casefold() in NOT_COPIED:
        return bad(f"is under `{parts[0]}/`, which isn't copied to the cells")
    known = files(root) if known is None else known
    by_fold = {}
    for f in known:
        by_fold.setdefault(f.casefold(), []).append(f)
    wanted = [p] if p.casefold().endswith(".md") else [p, p + ".md"]
    match = next((f for w in wanted for f in ([w] if w in known else by_fold.get(w.casefold(), [])[:1])), None)
    if not match and len(parts) == 1:
        # A bare name, as Obsidian's links allow: the one note of that name, wherever it is.
        names = [f for f in known if f.rsplit("/", 1)[-1].casefold() in {w.casefold() for w in wanted}]
        if len(names) > 1:
            return bad("could be " + " or ".join(f"`{_safe(n)}`" for n in names[:4]) + "; give the path")
        match = names[0] if names else None
    real = _inside(top, match) if match else None
    if not real or not os.path.isfile(real):
        d = _inside(top, p)
        if d and os.path.isdir(d):
            return bad("is a folder; list the notes in it that this work depends on")
        # A hint goes by the note's own name: a near name, in the folder that was named first.
        folder, _, base = wanted[-1].casefold().rpartition("/")
        by_name = {}
        for f in known:
            by_name.setdefault(f.rsplit("/", 1)[-1].casefold(), []).append(f)
        near = [f for b in difflib.get_close_matches(base, list(by_name), n=3, cutoff=0.8) for f in by_name[b]]
        near = sorted(near, key=lambda f: f.casefold().rpartition("/")[0] != folder)[:2]
        hint = " (did you mean " + " or ".join(f"`{_safe(f)}`" for f in near) + "?)" if near else ""
        return bad("isn't in the studio brain" + hint, wait=True)
    try:
        st = os.stat(real)
        if st.st_size > MAX_HASH:
            return bad("is too large to be a note")
        with open(real, "rb") as f:
            data = f.read()
    except OSError as e:
        return bad(f"couldn't be read ({e.strerror or e})", wait=True)
    found = None
    if heading:
        if not match.casefold().endswith(".md") or len(data) > MAX_HEADING_SCAN:
            return bad("names a section, and only a note (`.md`) has sections to name")
        text = data.decode("utf-8", errors="replace")
        tries = heading_tries(heading, loose)
        found = find_heading(text, tries)
        if not found:
            have, which = headings(text), tries[-1]
            return {"ref": f"{match}#{which}"[:MAX_REF], "wait": True,
                    "problem": f"is a note with no heading \"{which[:80]}\""
                               + (" (it has: " + ", ".join(f"\"{h[:60]}\"" for h in have[:10]) + (", ..." if len(have) > 10 else "") + ")"
                                  if have else " (it has no headings)")}
    return {"path": match, "heading": found,
            "edited": dt.datetime.fromtimestamp(int(st.st_mtime), dt.timezone.utc).isoformat(),
            "sha": hashlib.sha256(data).hexdigest()[:16]}


def check(root, texts):
    """The "Read first" lists of an item's texts against the brain copy `root` (None: this cell
    has no copy): {"notes": the notes found, in order, each once; "problems": what's wrong with
    the rest; "listed": how many references and unreadable lines there were}. Web links are
    left to the job: they aren't notes."""
    refs, bad = [], []
    for t in texts:
        r, b = listed(t)
        refs += r
        bad += b
    refs = [r for r in refs if not re.match(r"(?i)^https?://", r[0])]
    out = {"notes": [], "problems": [], "listed": len(refs) + len(bad)}
    if not out["listed"]:
        return out
    if not root or not os.path.isdir(root):
        out["problems"].append({"ref": None, "wait": False,
                                "problem": "this cell has no copy of the studio brain, so it can't check or read the notes listed"})
        return out
    for line in bad:
        out["problems"].append({"ref": None, "line": line, "wait": False,
                                "problem": "isn't a note's path"})
    if len(refs) > MAX_NOTES:
        out["problems"].append({"ref": None, "wait": False,
                                "problem": f"the list names {len(refs)} notes, and {MAX_NOTES} is the most a job is asked to read first"})
        return out
    known, seen = files(root), set()
    for ref in refs:
        got = resolve(root, ref, known)
        key = ((got.get("path") or got.get("ref") or "").casefold(), (got.get("heading") or "").casefold())
        if key in seen:
            continue
        seen.add(key)
        out["problems" if "problem" in got else "notes"].append(got)
    return out


def changed(root, notes):
    """The notes that no longer stand as recorded: [{"path", "heading", "now": "edited" or
    "gone", "edited": when, if it's there}]. A note edited while its job ran was read in
    one version or the other, and the record should say so."""
    out = []
    if not root or not os.path.isdir(root):
        return out                  # no copy to compare with: nothing can be said
    top = os.path.realpath(root)
    for n in notes or []:
        real = _inside(top, n["path"])
        try:
            with open(real, "rb") as f:
                data = f.read(MAX_HASH + 1)
            mtime = int(os.stat(real).st_mtime)
        except (OSError, TypeError):
            out.append({"path": n["path"], "heading": n.get("heading"), "now": "gone"})
            continue
        if hashlib.sha256(data).hexdigest()[:16] != n.get("sha"):
            out.append({"path": n["path"], "heading": n.get("heading"), "now": "edited",
                        "edited": dt.datetime.fromtimestamp(mtime, dt.timezone.utc).isoformat()})
    return out


# --- words -------------------------------------------------------------------------------------


def _safe(s):
    """A path or heading as it goes inside a code span or a prompt: one line, no backticks."""
    return re.sub(r"[\x00-\x1f]", " ", str(s)).replace("`", "'")


def name(n):
    """A note as an issue would list it: `Games/sandbox/jump.md#The numbers`. In a code span,
    so nothing in a path or heading is read as markup or a mention."""
    return "`" + _safe(n.get("path") or n.get("ref") or "?") + (f"#{_safe(n['heading'])}" if n.get("heading") else "") + "`"


def problem(p):
    """One thing wrong with a list, as a phrase: "`Games/x.md` isn't in the studio brain"."""
    if p.get("line"):
        return f"the line `{_safe(p['line'])}` {p['problem']}"
    return f"{name(p)} {p['problem']}" if p.get("ref") else p["problem"]


def when(iso):
    """A recorded time in the cell's own zone, to the minute: `2026-11-27 14:05 MST`."""
    try:
        t = dt.datetime.fromisoformat(iso).timestamp()
    except (TypeError, ValueError):
        return "at an unknown time"
    return time.strftime("%Y-%m-%d %H:%M %Z", time.localtime(t)).strip()


def place(root, n):
    """Where a job finds a note: its file, and the section if one was named."""
    return f"`{root}/{_safe(n['path'])}`" + (f" (the section \"{_safe(n['heading'])}\")" if n.get("heading") else "")


def refusal(problems):
    """The note on an item that isn't started because of its list. It never opens a list of
    its own under a "Read first" title, so it can't be read back as one."""
    return ("Not started: this item's \"Read first\" list can't be followed as it stands.\n\n"
            + "\n".join(f"- {problem(p)}" for p in problems)
            + "\n\nList each note as its path from the brain's top folder, with `#Heading` for one section. "
            "Fix the list or the notes, then approve it again (`agent-request`).")


def ledger(notes, changes=()):
    """The lines a job's result carries about its required reading: each note and when it was
    last edited, as the cell found it when it claimed the item, and any that changed since."""
    if not notes:
        return ""
    now = {(c["path"], c.get("heading")): c for c in changes or ()}
    rows = []
    for n in notes:
        c = now.get((n["path"], n.get("heading")))
        after = "" if not c else ("; **removed while the job ran**" if c["now"] == "gone"
                                  else f"; **edited while the job ran** ({when(c.get('edited'))})")
        rows.append(f"- {name(n)}, last edited {when(n.get('edited'))}{after}")
    return ("**Read first** (the notes the item named, as they stood when the job began):\n" + "\n".join(rows) + "\n\n")
