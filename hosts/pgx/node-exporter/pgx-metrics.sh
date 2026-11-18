#!/bin/bash
# PGX: write GB10 GPU and vLLM numbers for node_exporter's textfile collector,
# every 15 s, for Prometheus (job "node", instance "pgx") and the operators' hub.
#
# - GPU: nvidia-smi. On GB10, memory.* reads [N/A] (unified memory); the host's
#   node_memory_MemAvailable_bytes is the memory number that matters.
# - vLLM: its own /metrics, fetched from the host over lxdbr0. vLLM's port has
#   no API key, so it stays private; only these numbers leave the box.
#   pgx_vllm_up is 0 when vLLM does not answer (stopped, starting, wedged).
#
# Installed by install.sh as /usr/local/bin/pgx-metrics.sh, run by
# pgx-metrics.service (a loop, so the journal isn't flooded by a timer).
set -u
DIR=/var/lib/prometheus/node-exporter
OUT=$DIR/pgx.prom
VLLM_PORT=11535
VLLM_LXC=inference-server
KEEP='^vllm:(num_requests_running|num_requests_waiting|kv_cache_usage_perc|prompt_tokens_total|generation_tokens_total|request_success_total|num_preemptions_total|spec_decode_num_accepted_tokens_total|spec_decode_num_drafts_total|time_to_first_token_seconds_(sum|count)|e2e_request_latency_seconds_(sum|count))[{ ]'

collect() {
  echo "pgx_metrics_last_run_timestamp_seconds $(date +%s)"

  local g
  if g=$(timeout 10 nvidia-smi --query-gpu=utilization.gpu,temperature.gpu,power.draw,clocks.sm \
           --format=csv,noheader,nounits 2>/dev/null) && [ -n "$g" ]; then
    echo "pgx_gpu_up 1"
    echo "$g" | head -1 | awk -F', *' '
      function out(n, v) { if (v ~ /^[0-9.]+$/) print n, v }
      { out("pgx_gpu_utilization_percent", $1); out("pgx_gpu_temperature_celsius", $2)
        out("pgx_gpu_power_watts", $3); out("pgx_gpu_sm_clock_mhz", $4) }'
  else
    echo "pgx_gpu_up 0"
  fi

  local ip m
  ip=$(timeout 10 /usr/sbin/lxc query "/1.0/instances/$VLLM_LXC/state" 2>/dev/null | python3 -c '
import json, sys
try:
    a = json.load(sys.stdin)["network"]["eth0"]["addresses"]
    print(next(x["address"] for x in a if x["family"] == "inet"))
except Exception:
    pass')
  if [ -n "$ip" ] && m=$(curl -sf -m 5 "http://$ip:$VLLM_PORT/metrics"); then
    echo "pgx_vllm_up 1"
    printf '%s\n' "$m" | grep -E "$KEEP"
  else
    echo "pgx_vllm_up 0"
  fi
}

mkdir -p "$DIR"
while true; do
  tmp=$(mktemp "$OUT.XXXXXX")
  collect > "$tmp" 2>/dev/null
  chmod 0644 "$tmp"
  mv -f "$tmp" "$OUT"
  [ "${1:-}" = "--once" ] && exit 0
  sleep 15
done
