#!/usr/bin/env bash
# lab-activity-textfile: open client connections and interactive logins on this host,
# as node_exporter textfile metrics. Counts only: no IPs, no usernames.
# Vault: 1-Projects/lab-activity-monitoring.
#
# Runs as root from /etc/cron.d/lab-activity every minute (install: see the vault
# runbook; same pattern as pve-backup-metrics.py). Read-only: ss, lsns, who.
#
# Every network namespace on the host is checked, so on a Proxmox node that covers
# each LXC and every Docker container inside it; on a VM (jf) it is just the host.
# A connection counts when its local port is one the namespace listens on (it's a
# client coming in, not this host calling out) and the peer isn't loopback.
#
# Writes:
#   lab_guest_connections{guest,vmid,port,peer}  established client connections
#       guest: LXC name; "host" for the Proxmox node itself; on a VM, the VM's
#              own short hostname (it is the service, e.g. jf)
#       peer: traefik (10.0.2.5, proxied web), docker (172.16/12, app-internal,
#             e.g. app <-> its database), lan (10/8, 192.168/16), internet (the
#             rest, e.g. torrent peers; game and web users arrive via traefik)
#   lab_host_logins                              interactive logins (who), agents and
#                                                ansible left out
#   lab_activity_textfile_last_run_timestamp_seconds
#
# Ports 9100 (node_exporter) and the monitoring LXC's own scrapes are left out.
set -euo pipefail

TEXTFILE_DIR=/var/lib/prometheus/node-exporter
OUT="$TEXTFILE_DIR/lab_activity.prom"
MONITORING_IP=10.1.0.5

# vmid -> name (Proxmox nodes only)
declare -A NAME=()
HOSTGUEST=$(hostname -s)
if command -v pct >/dev/null; then
  HOSTGUEST=host
  while read -r id _ _ name; do NAME[$id]=$name; done < <(pct list 2>/dev/null | awk 'NR>1 {print $1, $2, $3, $NF}')
fi

count_conns() {
  # one line per namespace: first PID; ss in that namespace
  lsns -t net -n -o NS,PID | sort -k1,1 -u | while read -r _ pid; do
    vmid=$(grep -o 'lxc/[0-9]*' "/proc/$pid/cgroup" 2>/dev/null | head -1 | cut -d/ -f2 || true)
    vmid=${vmid:-0}
    listen=" $(nsenter -t "$pid" -n ss -Htln 2>/dev/null | awk '{n=split($4,a,":"); print a[n]}' | sort -u | tr '\n' ' ') "
    nsenter -t "$pid" -n ss -Htn state established 2>/dev/null |
      awk -v L="$listen" -v id="$vmid" -v mon="$MONITORING_IP" '{
        n = split($3, l, ":"); p = l[n]
        peer = $4; sub(/:[0-9]+$/, "", peer); gsub(/^\[|\]$/, "", peer); sub(/^::ffff:/, "", peer)
        if (!index(L, " " p " ") || p == "9100") next
        if (peer ~ /^127\./ || peer == "::1" || peer == mon) next
        cls = (peer == "10.0.2.5") ? "traefik" : (peer ~ /^172\.(1[6-9]|2[0-9]|3[01])\./) ? "docker" \
            : (peer ~ /^(10\.|192\.168\.)/) ? "lan" : "internet"
        print id, p, cls
      }'
  done | sort | uniq -c
}

tmp=$(mktemp "$TEXTFILE_DIR/.lab_activity.XXXXXX")
trap 'rm -f "$tmp"' EXIT
{
  echo "# HELP lab_guest_connections Established client connections to a listening port."
  echo "# TYPE lab_guest_connections gauge"
  while read -r n vmid port peer; do
    if [[ $vmid == 0 ]]; then guest=$HOSTGUEST; else guest=${NAME[$vmid]:-ct$vmid}; fi
    printf 'lab_guest_connections{guest="%s",vmid="%s",port="%s",peer="%s"} %d\n' "$guest" "$vmid" "$port" "$peer" "$n"
  done < <(count_conns)
  echo "# HELP lab_host_logins Interactive logins on this host (agents and ansible left out)."
  echo "# TYPE lab_host_logins gauge"
  echo "lab_host_logins $(who | awk '$1 !~ /^(claude-agent|ansible|wintermute|metaversig-agent)$/' | wc -l)"
  echo "# HELP lab_activity_textfile_last_run_timestamp_seconds Last successful run."
  echo "# TYPE lab_activity_textfile_last_run_timestamp_seconds gauge"
  echo "lab_activity_textfile_last_run_timestamp_seconds $(date +%s)"
} > "$tmp"
chmod 0644 "$tmp"
mv "$tmp" "$OUT"
trap - EXIT
