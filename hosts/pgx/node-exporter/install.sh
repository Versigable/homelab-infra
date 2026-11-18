#!/bin/bash
# Install or update the PGX exporter. Run by hand as root on the PGX host
# (thinkstationpgx-0eda); there is no CI job for the PGX, which is co-owned with
# Emilio and Dave. Copy this directory over, then: sudo bash install.sh
#
# What it does:
#   - prometheus-node-exporter from apt (no recommends), listening on
#     10.1.0.4:9100 (LAB only), with the textfile collector on
#   - pgx-metrics.service: GB10 GPU + vLLM numbers into the textfile every 15 s
# Undo: systemctl disable --now pgx-metrics prometheus-node-exporter;
#       apt-get remove prometheus-node-exporter; rm the files installed below.
set -euo pipefail
cd "$(dirname "$0")"

apt-get update -q
DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends prometheus-node-exporter

install -m 0644 prometheus-node-exporter.default /etc/default/prometheus-node-exporter
install -d /etc/systemd/system/prometheus-node-exporter.service.d
install -m 0644 prometheus-node-exporter.override.conf /etc/systemd/system/prometheus-node-exporter.service.d/pgx.conf
install -d -m 0755 /var/lib/prometheus/node-exporter
install -m 0755 pgx-metrics.sh /usr/local/bin/pgx-metrics.sh
install -m 0644 pgx-metrics.service /etc/systemd/system/pgx-metrics.service

systemctl daemon-reload
systemctl enable --now pgx-metrics.service
systemctl restart pgx-metrics.service
systemctl enable prometheus-node-exporter
systemctl restart prometheus-node-exporter

sleep 3
systemctl is-active pgx-metrics prometheus-node-exporter
ss -ltnH 'sport = :9100'
