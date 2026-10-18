#!/bin/bash
# Proxmox hookscript for Terraform-built containers (homelab-infra iac/ansible, role
# pve_lxc_rootonly). Before a container starts, it waits for the NFS mounts listed in
# /etc/iac/lxc-wait-mounts/<vmid>, one path per line.
#
# TrueNAS boots on the same node, so after a node boot the share can lag behind; reading
# the path also triggers its systemd automount. After 10 minutes it gives up, which
# stops the container from starting on an empty mount point.
vmid=$1
phase=$2
[[ "$phase" == "pre-start" ]] || exit 0

list="/etc/iac/lxc-wait-mounts/${vmid}"
[[ -r "$list" ]] || exit 0

while read -r path; do
  [[ -n "$path" && "$path" != \#* ]] || continue
  for _ in $(seq 1 120); do
    # A hard NFS mount blocks while the server is down; only SIGKILL interrupts it.
    if timeout -s KILL 10 ls "$path/" >/dev/null 2>&1 && mountpoint -q "$path"; then
      continue 2
    fi
    sleep 5
  done
  echo "iac-lxc-wait-mounts: ${path} not mounted after 10 minutes; not starting CT ${vmid}" >&2
  exit 1
done < "$list"
exit 0
