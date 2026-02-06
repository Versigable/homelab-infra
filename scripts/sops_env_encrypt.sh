sops_env_encrypt() {
  # Encrypt a dotenv plaintext file into a SOPS-encrypted dotenv
  #
  # Usage:
  #   sops_env_encrypt <PLAINTEXT_ENV> <TARGET_ENV_SOPS>
  #
  # Example:
  #   sops_env_encrypt /home/wiki/wiki-hub/compose/.env \
  #     hosts/servicehub/secrets/compose.env.sops

  set -euo pipefail

  # ---- args ----
  if [[ $# -ne 2 ]]; then
    cat >&2 <<'EOF'
Usage: sops_env_encrypt <PLAINTEXT_ENV> <TARGET_ENV_SOPS>

Encrypt a dotenv file using SOPS, applying .sops.yaml creation_rules
via --filename-override.

Arguments:
  PLAINTEXT_ENV      Path to plaintext .env file
  TARGET_ENV_SOPS    Repo-relative path ending in .env.sops

Example:
  sops_env_encrypt /home/wiki/wiki-hub/compose/.env \
    hosts/servicehub/secrets/compose.env.sops
EOF
    return 2
  fi

  local plaintext="$1"
  local target="$2"

  # ---- sanity checks ----
  if ! command -v sops >/dev/null 2>&1; then
    echo "ERROR: sops not found in PATH" >&2
    return 127
  fi

  if [[ ! -f "$plaintext" ]]; then
    echo "ERROR: plaintext file not found: $plaintext" >&2
    return 3
  fi

  if [[ ! -r "$plaintext" ]]; then
    echo "ERROR: plaintext file not readable: $plaintext" >&2
    return 4
  fi

  local target_dir
  target_dir="$(dirname "$target")"
  if [[ ! -d "$target_dir" ]]; then
    echo "ERROR: target directory does not exist: $target_dir" >&2
    return 5
  fi

  if [[ "$target" != *.sops ]]; then
    echo "ERROR: target must end in .sops (got: $target)" >&2
    return 6
  fi

  # ---- encrypt ----
  # IMPORTANT:
  # - filename-override is what creation_rules match against
  # - input path is ignored for rule matching
  #
  # We do NOT echo secrets or enable xtrace here.
  umask 077

  sops --encrypt \
    --input-type dotenv \
    --output-type dotenv \
    --filename-override "$target" \
    --output "$target" \
    "$plaintext"

  echo "✓ Encrypted: $target"
}
