#!/usr/bin/env python3
"""Export Proxmox backup freshness as node_exporter textfile metrics.

Answers "when was each guest last backed up successfully?" -- deliberately measured
from the *backup store*, not from the backup job's own success reporting. A job that
never runs, is disabled, or whose schedule silently stops firing produces no failure
notification at all; it only shows up as a backup that keeps getting older. That is
the failure mode that went unnoticed for 99 days (2026-05-31 -> 2026-09-07).

Writes:
  pve_backup_last_success_timestamp_seconds{vmid,name,type}  newest backup, unix time
  pve_backup_guest_without_backup{vmid,name,type}            1 = guest has NO backup
  pve_backup_guests_total                                    guests currently defined
  pve_backup_metrics_last_run_timestamp_seconds              this script's last success

Optionally pings a Healthchecks.io URL so the *checker's* own death is detectable
from outside this fault domain (same pattern the monitoring stack uses for itself).
Config: /etc/pve-backup-metrics.conf  ->  HC_PING_URL=https://hc-ping.com/<uuid>
"""
import json, os, re, subprocess, sys, tempfile, time
from datetime import datetime, timezone

STORAGES = ["PBS-4TB-SSD", "PBS"]
TEXTFILE_DIR = "/var/lib/prometheus/node-exporter"
OUT = os.path.join(TEXTFILE_DIR, "pve_backup.prom")
CONF = "/etc/pve-backup-metrics.conf"
VOLID_RE = re.compile(r"backup/(ct|vm)/(\d+)/(\d{4}-\d{2}-\d{2}T[\d:]+Z)")


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120)


def guests():
    """Currently-defined guests, cluster-wide. Backups for guests that no longer
    exist are ignored on purpose -- stale groups shouldn't mask a real gap."""
    p = run(["pvesh", "get", "/cluster/resources", "--type", "vm", "--output-format", "json"])
    if p.returncode != 0:
        raise RuntimeError("pvesh get /cluster/resources failed: %s" % p.stderr.strip())
    out = {}
    for r in json.loads(p.stdout):
        vmid = r.get("vmid")
        if vmid is None:
            continue
        out[int(vmid)] = {
            "name": r.get("name") or "",
            # pvesh reports 'qemu'/'lxc'; backup volids use 'vm'/'ct'
            "type": "vm" if r.get("type") == "qemu" else "ct",
        }
    return out


def newest_backups():
    """vmid -> newest backup epoch, across every configured PBS datastore."""
    newest = {}
    for st in STORAGES:
        p = run(["pvesm", "list", st])
        if p.returncode != 0:
            continue  # datastore offline: leave gap visible rather than inventing data
        for line in p.stdout.splitlines()[1:]:
            m = VOLID_RE.search(line.split()[0] if line.split() else "")
            if not m:
                continue
            vmid = int(m.group(2))
            ts = datetime.strptime(m.group(3), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            epoch = ts.timestamp()
            if epoch > newest.get(vmid, 0):
                newest[vmid] = epoch
    return newest


def esc(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')


def main():
    g = guests()
    b = newest_backups()

    lines = [
        "# HELP pve_backup_last_success_timestamp_seconds Unix time of the newest backup present in PBS for this guest.",
        "# TYPE pve_backup_last_success_timestamp_seconds gauge",
    ]
    for vmid, meta in sorted(g.items()):
        if vmid in b:
            lines.append('pve_backup_last_success_timestamp_seconds{vmid="%d",name="%s",type="%s"} %d'
                         % (vmid, esc(meta["name"]), meta["type"], int(b[vmid])))

    lines += [
        "# HELP pve_backup_guest_without_backup 1 if a currently-defined guest has no backup at all.",
        "# TYPE pve_backup_guest_without_backup gauge",
    ]
    for vmid, meta in sorted(g.items()):
        lines.append('pve_backup_guest_without_backup{vmid="%d",name="%s",type="%s"} %d'
                     % (vmid, esc(meta["name"]), meta["type"], 0 if vmid in b else 1))

    lines += [
        "# HELP pve_backup_guests_total Guests currently defined in the cluster.",
        "# TYPE pve_backup_guests_total gauge",
        "pve_backup_guests_total %d" % len(g),
        "# HELP pve_backup_metrics_last_run_timestamp_seconds Unix time this exporter last completed successfully.",
        "# TYPE pve_backup_metrics_last_run_timestamp_seconds gauge",
        "pve_backup_metrics_last_run_timestamp_seconds %d" % int(time.time()),
    ]

    os.makedirs(TEXTFILE_DIR, exist_ok=True)
    # Atomic replace: node_exporter must never read a half-written file.
    fd, tmp = tempfile.mkstemp(dir=TEXTFILE_DIR, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, OUT)

    hc = ""
    if os.path.exists(CONF):
        for line in open(CONF):
            if line.strip().startswith("HC_PING_URL="):
                hc = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
    if hc:
        # Best effort: a failed ping must not fail the metrics write.
        subprocess.run(["curl", "-fsS", "-m", "10", "-o", "/dev/null", hc], check=False)

    print("wrote %s: %d guests, %d with backups, %d without"
          % (OUT, len(g), len(b & g.keys() if isinstance(b, set) else set(b) & set(g)),
             len(set(g) - set(b))))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("ERROR: %s" % e, file=sys.stderr)
        sys.exit(1)
