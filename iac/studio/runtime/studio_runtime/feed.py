"""The builds channel, readable at a glance (studio-slackd; vault decision 2026-11-17, "huddles
file themselves, and a builds channel you can read at a glance").

Each piece of work is one message in the channel, and that message always shows where the work
stands now: building, ready with its Play button, reviewed, played, merged. The thread under it
keeps the story, one reply per event. The text here is Slack's mrkdwn; nothing in this module
talks to Slack.
"""

import re

from . import common

# How the work itself stands; the review and the plays are told beside it.
WORK = {"building": ":hammer_and_wrench: building", "reviewing": ":mag: review running",
        "ready": ":package:", "merged": ":white_check_mark:", "closed": ":wastebasket:",
        "blocked": ":construction: blocked", "failed": ":x: failed", "stopped": ":octagonal_sign: stopped",
        "refused": ":no_entry: not started", "void": ":no_entry: not started", "waiting": ":hourglass: waiting"}
LABELS = {"blocked": ":construction: Blocked", "failed": ":x: Failed", "stopped": ":octagonal_sign: Stopped",
          "refused": ":no_entry: Not started", "void": ":no_entry: Not started", "waiting": ":hourglass: Waiting"}
OVER = ("merged", "closed")


def esc(s):
    return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def short(ref):
    """studio/sandbox#9 as sandbox#9."""
    return (ref or "?").split("/", 1)[-1] or "?"


def link(url, text):
    return f"<{url}|{esc(text)}>" if url else esc(text)


def mr_mark(mr):
    """An MR as !8, linked."""
    ref = (mr or {}).get("ref") or ""
    return link((mr or {}).get("url"), "!" + ref.rsplit("!", 1)[-1] if "!" in ref else short(ref))


def plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


# --- an item's entry: what its message shows -------------------------------------------------------

def new_entry(e, channel=None, ts=None):
    item = e.get("item") or {}
    return {"channel": channel, "ts": ts, "ref": item.get("ref") or "", "title": item.get("title") or "",
            "url": item.get("url") or "", "kind": item.get("kind") or "issue", "state": None}


def apply(entry, e):
    """The entry after event e. Returns the entry."""
    kind, item, mr = e.get("event"), e.get("item") or {}, e.get("mr") or {}
    if not entry.get("ref") and item.get("ref"):
        entry.update(ref=item["ref"], url=item.get("url") or entry.get("url") or "",
                     kind=item.get("kind") or entry.get("kind") or "issue")
    if item.get("title") and not entry.get("title"):
        entry["title"] = item["title"]
    if e.get("vendor") and kind in ("claimed", "waiting"):
        entry["vendor"] = e["vendor"]
    if mr.get("ref") and kind in ("done", "merged", "closed", "played", "review"):
        entry["mr"] = {"ref": mr["ref"], "url": mr.get("url") or (entry.get("mr") or {}).get("url") or ""}
    if kind == "claimed":
        review_job = item.get("kind") == "mr"
        entry["state"] = "reviewing" if review_job else "building"
        if not review_job:
            entry.pop("note", None)
    elif kind == "done":
        entry["state"] = "ready"
        entry["gates"] = e.get("gates") or ""
        entry["play_url"] = e.get("play_url") or ""
        entry.pop("review", None)          # a new revision: the last review and plays were of the old one
        entry.pop("played", None)
    elif kind == "review":
        findings = e.get("findings")
        if findings is None:
            findings = common.review_digest(e.get("text"))[1]
        entry["review"] = {"vendor": e.get("vendor") or "?", "findings": len(findings)}
        if entry.get("state") == "reviewing":
            entry["state"] = "ready"
    elif kind == "played":
        plays = entry.setdefault("played", {})
        who = e.get("person") or "someone"
        plays[who] = plays.get(who, 0) + int(e.get("minutes") or 0)
        if e.get("play_url") and not entry.get("play_url"):
            entry["play_url"] = e["play_url"]
    elif kind in OVER:
        entry["state"] = kind
        entry["by"] = e.get("person") or ""
    elif kind in LABELS:
        entry["state"] = kind
        entry["note"] = one_line(e.get("text"), 140)
    return entry


def one_line(text, n):
    text = re.sub(r"\s+", " ", (text or "").strip())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def head(entry, owner_name):
    """(text, blocks) of an item's message: its link and title, then where it stands."""
    title = link(entry.get("url"), short(entry.get("ref"))) + (
        f" *{esc(entry['title'])}*" if entry.get("title") else "")
    state, mr, parts = entry.get("state"), entry.get("mr"), []
    if state == "building":
        parts.append(f"{WORK[state]} on {esc(owner_name)}'s cell" + (f" ({esc(entry['vendor'])})" if entry.get("vendor") else ""))
    elif state == "reviewing":
        parts.append(WORK[state] + (f" ({esc(entry['vendor'])})" if entry.get("vendor") else ""))
    elif state == "ready":
        parts.append(f"{WORK[state]} {mr_mark(mr) if mr else 'MR'} ready"
                     + (" · gates passed" if (entry.get("gates") or "").startswith("passed") else ""))
    elif state in OVER:
        parts.append(f"{WORK[state]} {mr_mark(mr) + ' ' if mr else ''}{state}"
                     + (f" by {esc(entry['by'])}" if entry.get("by") else ""))
    elif state in WORK:
        parts.append(WORK[state] + (f": {esc(entry['note'])}" if entry.get("note") else ""))
    rv = entry.get("review")
    if rv:
        n = rv.get("findings") or 0
        parts.append(f":mag: {esc(rv.get('vendor'))}: {plural(n, 'finding')}" if n
                     else f":mag: reviewed by {esc(rv.get('vendor'))}")
    plays = entry.get("played") or {}
    if plays:
        parts.append(":video_game: played by " + ", ".join(f"{esc(p)} ({m} min)" for p, m in plays.items()))
    elif state == "ready":
        parts.append(":video_game: not played yet")
    text = title + ("\n" + " · ".join(parts) if parts else "")
    if entry.get("newer"):
        text += f"\n_Picked up again: {link(entry['newer'], 'the newer thread')}_"
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
    buttons = []
    if entry.get("play_url") and state not in OVER and not entry.get("newer"):
        b = {"type": "button", "text": {"type": "plain_text", "text": "Play it"}, "url": entry["play_url"],
             "action_id": "feed_play"}
        if state == "ready" and not plays:
            b["style"] = "primary"
        buttons.append(b)
    if (mr or {}).get("url"):
        buttons.append({"type": "button", "text": {"type": "plain_text", "text": "Open the MR"}, "url": mr["url"],
                        "action_id": "feed_mr"})
    if buttons:
        blocks.append({"type": "actions", "elements": buttons})
    return text, blocks


# --- the thread's replies ----------------------------------------------------------------------------

def reply(e, owner_name, mrkdwn, ping=None):
    """One event as a line (or a few) in the item's thread. ping is the owner's Slack ID when
    the event is theirs to act on, so Slack notifies them."""
    kind, item, mr = e.get("event"), e.get("item") or {}, e.get("mr") or {}
    ilink = link(item.get("url"), short(item.get("ref")))
    mlink = link(mr.get("url"), short(mr.get("ref"))) if mr.get("ref") else ""
    text = cut(e.get("text"), 1200)
    if kind == "claimed":
        what = "Review claimed" if item.get("kind") == "mr" else "Claimed"
        return f"{what} by {esc(owner_name)}'s cell ({esc(e.get('vendor') or '?')})"
    if kind == "done":
        return (f":package: MR ready: {mlink}" + (f" · gates {esc(e['gates'])}" if e.get("gates") else "")
                + (f" · <{e['play_url']}|Play it>" if e.get("play_url") else "")
                + (f" · <@{ping}>" if ping else ""))
    if kind == "review":
        return review_reply(e, mlink or ilink)
    if kind == "played":
        return f":video_game: Played by {esc(e.get('person') or 'someone')} for {e.get('minutes') or '?'} min"
    if kind == "merged":
        return f":white_check_mark: Merged{' by ' + esc(e['person']) if e.get('person') else ''}: {mlink or ilink}"
    if kind == "closed":
        return f":wastebasket: Closed without merging{' by ' + esc(e['person']) if e.get('person') else ''}: {mlink or ilink}"
    return f"{LABELS.get(kind, kind or '?')}: {ilink}" + (f"\n{mrkdwn(text)}" if text else "")


def cut(text, n):
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def review_reply(e, where):
    """A review as its opening words, its findings' titles and a link to the whole of it; never
    the text cut off mid-sentence."""
    summary, findings = e.get("summary"), e.get("findings")
    if summary is None and findings is None:          # an event written before agentd sent these
        summary, findings = common.review_digest(e.get("text"))
    findings = findings or []
    vendor = esc(e.get("vendor") or "?")
    lines = [f":mag: Review by {vendor} on {where}" + (f": {plural(len(findings), 'finding')}" if findings else "")]
    if summary:
        lines.append(f"&gt; {esc(summary)}")
    lines += [f"• *{esc(sev)}* {esc(title)}" for sev, title in findings]
    url = e.get("note_url") or (e.get("mr") or {}).get("url")
    if url:
        lines.append(f"<{url}|Read the full review>")
    return "\n".join(lines)


# --- the morning list -----------------------------------------------------------------------------

def morning(rows, others):
    """The owner's morning DM: their open work first, then what else is open. rows and others are
    lists of {"mark": link, "title", "notes": [..], "play_url"}; None when there's nothing."""
    if not rows and not others:
        return None

    def line(r):
        tail = "; ".join(r.get("notes") or [])
        return (f"• {r['mark']} {esc(r.get('title'))}" + (f": {tail}" if tail else "")
                + (f" · <{r['play_url']}|Play it>" if r.get("play_url") else ""))
    out = []
    if rows:
        out += ["*Waiting on you*"] + [line(r) for r in rows]
    if others:
        out += ["*Also open*"] + [line(r) for r in others]
    return "\n".join(out)
