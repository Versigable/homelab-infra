#!/bin/sh
# Nextcloud settings as code. The image runs this as www-data on every start of the
# app container, after any install or upgrade, so every step is idempotent.
#
# A hook that exits non-zero stops the container from starting. Each step therefore
# only warns; after a deploy, check `docker logs nextcloud-app` for "WARN".
set -u

failures=0
occ() { php /var/www/html/occ --no-interaction "$@"; }

# run <label> <command...>: show the output only when the step fails.
run() {
    label=$1
    shift
    if ! out=$("$@" 2>&1); then
        echo "==> WARN: ${label} failed: ${out}"
        failures=$((failures + 1))
    fi
}

# --- Address and proxies ------------------------------------------------------
# Traffic arrives Cloudflare -> cloudflared -> Traefik (10.0.2.5) -> web. Traefik
# rewrites X-Forwarded-For to the cloudflared hop, so the real client IP (needed for
# brute-force throttling) comes from CF-Connecting-IP, as for ntfy.
run "trusted domain"  occ config:system:set trusted_domains 0 --value=cloud.ninjaprivacy.org
run "trusted proxy"   occ config:system:set trusted_proxies 0 --value=10.0.2.5
run "trusted proxy"   occ config:system:set trusted_proxies 1 --value=172.30.110.0/24
run "client IP"       occ config:system:set forwarded_for_headers 0 --value=HTTP_CF_CONNECTING_IP
run "https"           occ config:system:set overwriteprotocol --value=https
run "cli url"         occ config:system:set overwrite.cli.url --value=https://cloud.ninjaprivacy.org
run "phone region"    occ config:system:set default_phone_region --value=US
# Heavy background jobs start at 08:00 UTC (02:00 MDT).
run "maint. window"   occ config:system:set maintenance_window_start --type=integer --value=8
run "cron"            occ background:cron

# --- Apps -------------------------------------------------------------------
# Photos ships with the server; the others come from the app store.
for app in calendar contacts tasks richdocuments user_oidc photos; do
    occ app:enable "$app" >/dev/null 2>&1 || run "install ${app}" occ app:install "$app"
done

# --- Quotas and sharing defaults ------------------------------------------------
run "default quota"   occ config:app:set files default_quota --value="1 TB"
run "link expiry"     occ config:app:set core shareapi_default_expire_date --value=yes
run "link expiry"     occ config:app:set core shareapi_expire_after_n_days --value=30
run "link password"   occ config:app:set core shareapi_enable_link_password_by_default --value=yes

# --- Login through Authentik ----------------------------------------------------
# People sign in only through OIDC; the break-glass admin uses /login?direct=1.
# Output is dropped here so a failure can't echo the client secret.
if [ -n "${NEXTCLOUD_OIDC_CLIENT_ID:-}" ]; then
    if ! occ user_oidc:provider Authentik \
        --clientid="$NEXTCLOUD_OIDC_CLIENT_ID" \
        --clientsecret="$NEXTCLOUD_OIDC_CLIENT_SECRET" \
        --discoveryuri=https://auth.ninjaprivacy.org/application/o/nextcloud/.well-known/openid-configuration \
        --scope="openid email profile" \
        --unique-uid=0 \
        --mapping-uid=preferred_username \
        --mapping-display-name=name \
        --mapping-email=email >/dev/null 2>&1; then
        echo "==> WARN: OIDC provider setup failed (rerun the occ command by hand to see why)"
        failures=$((failures + 1))
    fi
    run "OIDC only"   occ config:app:set user_oidc allow_multiple_user_backends --value=0
fi

# --- Collabora ------------------------------------------------------------------
# Nextcloud reaches Collabora inside the compose network; browsers use office.*.
run "office url"      occ config:app:set richdocuments wopi_url --value=http://collabora:9980
run "office url"      occ config:app:set richdocuments public_wopi_url --value=https://office.ninjaprivacy.org
run "office config"   occ richdocuments:activate-config

echo "==> settings hook finished with ${failures} warning(s)"
exit 0
