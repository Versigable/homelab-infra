"""`studio docs`: Unity's documentation for the pinned Editor, offline, as text.

The Editor's documentation module (installed by role studio_cell) holds the whole Script
Reference and Manual as HTML under <Editor>/Data/Documentation/Documentation/en, readable by
everyone. `unity docs` would fetch an index from docs.unity.com, and part of the CLI ignores
the proxy for it (2026-10-31: NETWORK_UNREACHABLE for every user on a cell, through any
proxy), so this reads the pages on disk instead: no network, and exactly the pinned version.
"""

import html
import os
import re

from . import common

MAX_MATCHES = 25
# The feedback form every page carries, as it comes out in text.
FORM_START, FORM_END = "Success!", "Cancel"


def docs_root():
    editor = common.load_config()["unity_editor"]
    return os.path.join(os.path.dirname(editor), "Data", "Documentation", "Documentation", "en")


def page_text(path):
    """A page as plain text: headings, declarations, tables, and code with its indentation."""
    with open(path, encoding="utf-8", errors="replace") as f:
        t = f.read()
    i = t.find("<h1")
    t = t[i:] if i >= 0 else t
    for end in ('<div class="footer', '<div id="footer', "<footer"):
        j = t.find(end)
        if j >= 0:
            t = t[:j]
    t = re.sub(r"(?s)<(script|style|nav)\b.*?</\1>", " ", t)
    # Code keeps its lines and indentation: set aside, then put back.
    pres = []

    def keep(m):
        code = re.sub(r"(?i)<br\s*/?>", "\n", m.group(1))
        code = html.unescape(re.sub(r"<[^>]+>", "", code)).strip("\n")
        pres.append(code)
        return f"\n\x00{len(pres) - 1}\x00\n"

    t = re.sub(r"(?s)<pre\b[^>]*>(.*?)</pre>", keep, t)
    t = re.sub(r"(?i)<br\s*/?>|</(p|div|h[1-6]|tr|li|table)>", "\n", t)
    t = re.sub(r"(?i)</t[dh]>", " | ", t)
    t = html.unescape(re.sub(r"<[^>]+>", "", t))
    out, in_form = [], False
    for line in t.splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line == FORM_START:
            in_form = True
        if in_form:
            in_form = line != FORM_END
            continue
        if not line or line in ("Leave feedback", "Suggest a change", "Switch to Manual", "Switch to Scripting", "|"):
            continue
        m = re.fullmatch("\x00(\\d+)\x00", line)
        out.append(pres[int(m.group(1))] if m else line)
    return "\n".join(out)


def find(section, query):
    """Exact page, else the pages whose names contain the query (case-insensitive)."""
    base = os.path.join(docs_root(), section)
    candidates = [query]
    if section == "ScriptReference" and "." in query:
        head, _, tail = query.rpartition(".")
        candidates.append(f"{head}-{tail}")      # properties: Transform-localPosition
    for c in candidates:
        p = os.path.join(base, c + ".html")
        if os.path.isfile(p):
            return p, []
    q = query.lower().replace("-", ".")
    names = sorted(n[:-5] for n in os.listdir(base) if n.endswith(".html") and q in n[:-5].lower().replace("-", "."))
    exact = [n for n in names if n.lower().replace("-", ".") == q]
    if len(exact) == 1 or len(names) == 1:
        return os.path.join(base, (exact or names)[0] + ".html"), []
    return None, names


def main(args):
    section = "ScriptReference"
    if args[:1] == ["--manual"]:
        section, args = "Manual", args[1:]
    if len(args) != 1:
        print("usage: studio docs [--manual] <name>   e.g. studio docs Mathf.Sin, studio docs --manual Prefabs")
        return 2
    try:
        path, names = find(section, args[0])
    except OSError as e:
        print(f"studio docs: the documentation isn't installed here ({e})")
        return 1
    if path:
        print(page_text(path))
        return 0
    if not names:
        print(f"studio docs: no {section} page matches {args[0]!r}")
        return 1
    print(f"{len(names)} {section} pages match {args[0]!r}"
          + (f" (the first {MAX_MATCHES})" if len(names) > MAX_MATCHES else "") + ":")
    print("\n".join(f"  {n}" for n in names[:MAX_MATCHES]))
    return 1
