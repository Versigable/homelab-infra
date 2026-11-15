"""GitLab for studio-agentd, as the cell's agent account (/etc/studio/gitlab-token, root only).

The cell reaches GitLab the way the internet does: the public hostname, through Squid's
default class and Cloudflare, with a real User-Agent (Cloudflare answers Python's default one
with error 1010). Only the daemon holds the token; a job never sees it.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

from . import VERSION

TOKEN_FILE = "/etc/studio/gitlab-token"


class GitLabError(Exception):
    def __init__(self, status, body):
        super().__init__(f"GitLab HTTP {status}: {str(body)[:300]}")
        self.status = status


class GitLab:
    def __init__(self, cfg):
        self.base = cfg["gitlab_url"].rstrip("/") + "/api/v4"
        self.group = cfg["gitlab_group"]
        with open(TOKEN_FILE, encoding="utf-8") as f:
            self._token = f.read().strip()
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"https": cfg["root_proxy"], "http": cfg["root_proxy"]}))
        self.ua = f"studio-agentd/{VERSION} ({cfg['gitlab_agent']})"

    def call(self, method, path, data=None, params=None):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("PRIVATE-TOKEN", self._token)
        req.add_header("User-Agent", self.ua)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with self.opener.open(req, timeout=60) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raise GitLabError(e.code, e.read()[:500]) from None

    def get_all(self, path, params=None, limit=500):
        out, page = [], 1
        while len(out) < limit:
            batch = self.call("GET", path, params={**(params or {}), "per_page": 100, "page": page})
            out += batch or []
            if not batch or len(batch) < 100:
                break
            page += 1
        return out

    @staticmethod
    def pid(project):
        return urllib.parse.quote(str(project), safe="")

    # --- reads -----------------------------------------------------------------------------

    def graphql(self, query, variables):
        url = self.base[: -len("/api/v4")] + "/api/graphql"
        req = urllib.request.Request(url, data=json.dumps({"query": query, "variables": variables}).encode(),
                                     method="POST")
        req.add_header("PRIVATE-TOKEN", self._token)
        req.add_header("User-Agent", self.ua)
        req.add_header("Content-Type", "application/json")
        try:
            with self.opener.open(req, timeout=60) as r:
                d = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise GitLabError(e.code, e.read()[:500]) from None
        if d.get("errors"):
            raise GitLabError(200, d["errors"])
        return d["data"]

    def last_edited(self, kind, full_path, iid):
        """When an issue's description was last edited: REST doesn't say, and GraphQL keeps it on
        the work item's description widget (GitLab 18.8: Issue and MergeRequest have only
        updatedAt, which every label change moves). MRs have no such field: None."""
        if kind != "issue":
            return None
        d = self.graphql("query($p: ID!, $i: String!) { project(fullPath: $p) { workItems(iid: $i) "
                         "{ nodes { widgets { ... on WorkItemWidgetDescription { lastEditedAt } } } } } }",
                         {"p": full_path, "i": str(iid)})
        nodes = (((d.get("project") or {}).get("workItems") or {}).get("nodes")) or []
        for w in (nodes[0].get("widgets") if nodes else []) or []:
            if w.get("lastEditedAt"):
                return w["lastEditedAt"]
        return None

    def me(self):
        return self.call("GET", "/user")

    def requested(self, label):
        """Open issues and MRs in the group carrying the label."""
        g = self.pid(self.group)
        q = {"labels": label, "state": "opened", "scope": "all"}
        return ([("issue", i) for i in self.get_all(f"/groups/{g}/issues", q)]
                + [("mr", m) for m in self.get_all(f"/groups/{g}/merge_requests", q)])

    def label_events(self, kind, project_id, iid):
        what = "issues" if kind == "issue" else "merge_requests"
        return self.get_all(f"/projects/{project_id}/{what}/{iid}/resource_label_events")

    def item(self, kind, project_id, iid):
        what = "issues" if kind == "issue" else "merge_requests"
        return self.call("GET", f"/projects/{project_id}/{what}/{iid}")

    def notes(self, kind, project_id, iid):
        what = "issues" if kind == "issue" else "merge_requests"
        return self.get_all(f"/projects/{project_id}/{what}/{iid}/notes", {"sort": "asc", "order_by": "created_at"})

    def project(self, project_id):
        return self.call("GET", f"/projects/{project_id}")

    def mr_for_branch(self, project_id, branch):
        mrs = self.call("GET", f"/projects/{project_id}/merge_requests",
                        params={"source_branch": branch, "state": "opened"})
        return mrs[0] if mrs else None

    def mr_diff(self, project_id, iid):
        return self.get_all(f"/projects/{project_id}/merge_requests/{iid}/diffs", limit=300)

    # --- writes ----------------------------------------------------------------------------

    def relabel(self, kind, project_id, iid, add=(), remove=()):
        what = "issues" if kind == "issue" else "merge_requests"
        return self.call("PUT", f"/projects/{project_id}/{what}/{iid}",
                         {"add_labels": ",".join(add), "remove_labels": ",".join(remove)})

    def note(self, kind, project_id, iid, body):
        what = "issues" if kind == "issue" else "merge_requests"
        return self.call("POST", f"/projects/{project_id}/{what}/{iid}/notes", {"body": body})

    def open_mr(self, project_id, branch, target, title, description, labels):
        return self.call("POST", f"/projects/{project_id}/merge_requests",
                         {"source_branch": branch, "target_branch": target, "title": title,
                          "description": description, "labels": ",".join(labels),
                          "remove_source_branch": True})

    def update_mr(self, project_id, iid, **fields):
        return self.call("PUT", f"/projects/{project_id}/merge_requests/{iid}", fields)

    def status(self, project_id, sha, state, name, description, target_url=None):
        data = {"state": state, "name": name, "description": description[:255]}
        if target_url:
            data["target_url"] = target_url
        return self.call("POST", f"/projects/{project_id}/statuses/{sha}", data)

    def new_issue(self, project_id, title, description, labels):
        return self.call("POST", f"/projects/{project_id}/issues",
                         {"title": title, "description": description, "labels": ",".join(labels)})
