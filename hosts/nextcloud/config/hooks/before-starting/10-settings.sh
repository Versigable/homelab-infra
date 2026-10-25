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
# The first install also wrote it at index 1; deleting a missing entry is a no-op.
run "trusted domain"  occ config:system:delete trusted_domains 1
run "trusted proxy"   occ config:system:set trusted_proxies 0 --value=10.0.2.5
run "trusted proxy"   occ config:system:set trusted_proxies 1 --value=172.30.110.0/24
run "client IP"       occ config:system:set forwarded_for_headers 0 --value=HTTP_CF_CONNECTING_IP
run "https"           occ config:system:set overwriteprotocol --value=https
run "cli url"         occ config:system:set overwrite.cli.url --value=https://cloud.ninjaprivacy.org
run "phone region"    occ config:system:set default_phone_region --value=US
# Heavy background jobs start at 08:00 UTC (02:00 MDT).
run "maint. window"   occ config:system:set maintenance_window_start --type=integer --value=8
run "cron"            occ background:cron
# One server, so any id in 0-511 will do.
run "server id"       occ config:system:set serverid --type=integer --value=1

# --- Repairs after an install or upgrade ------------------------------------------
# Mimetype migrations and missing DB indices are left to the admin after each new
# version. Run them once per version.
version=$(occ config:system:get version)
if [ "$(occ config:app:get homelab repaired_version 2>/dev/null)" != "$version" ]; then
    before=$failures
    run "repair"          occ maintenance:repair --include-expensive
    run "db indices"      occ db:add-missing-indices
    if [ "$failures" -eq "$before" ]; then
        run "repair marker" occ config:app:set homelab repaired_version --value="$version"
    fi
fi

# --- Apps -------------------------------------------------------------------
# Photos and TOTP ship with the server; the others come from the app store, which
# app:enable downloads from. The store's index is large and sometimes very slow; after
# a failed fetch Nextcloud won't try again for 5 minutes, so clear that and retry.
# end_to_end_encryption only lets the desktop and mobile clients encrypt folders; people
# opt in per folder, and the server never sees their keys.
# groupfolders ("Team Folders" in the UI) holds the game studio's shared Studio folder.
missing="calendar contacts tasks richdocuments user_oidc photos twofactor_totp end_to_end_encryption groupfolders"
for attempt in 1 2 3; do
    still=""
    for app in $missing; do
        if ! out=$(occ app:enable "$app" 2>&1); then
            still="$still $app"
            last_error=$out
        fi
    done
    missing=$still
    if [ -z "$missing" ] || [ "$attempt" -eq 3 ]; then
        break
    fi
    occ config:app:delete settings appstore-fetcher-lastFailure >/dev/null 2>&1
    sleep 15
done
for app in $missing; do
    echo "==> WARN: install ${app} failed after 3 attempts: ${last_error}"
    failures=$((failures + 1))
done

# Not using External Apps (Ex-Apps), so AppAPI's deploy daemon warning is noise.
if occ app:list --enabled | grep -q -- '- app_api:'; then
    run "disable app_api" occ app:disable app_api
fi

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

# --- Break-glass admin ------------------------------------------------------------
# Its /login?direct=1 form is public, so it gets a second factor: Nextcloud makes it set
# up TOTP at its next login. People sign in through Authentik and aren't in this group.
# Recovery if the TOTP device is lost: `occ twofactorauth:disable <user> totp`.
if [ -n "${NEXTCLOUD_ADMIN_USER:-}" ]; then
    occ group:info breakglass >/dev/null 2>&1 || run "breakglass group" occ group:add breakglass
    run "breakglass member" occ group:adduser breakglass "$NEXTCLOUD_ADMIN_USER"
    run "breakglass 2FA"    occ twofactorauth:enforce --on --group=breakglass
fi

# --- Game studio shared folder ---------------------------------------------------
# One Team Folder, "Studio", for the Nextcloud group "studio": references, mood boards and
# voice memos people hand to the studio (vault: 1-Projects/game-studio-platform/design/).
# Members are listed here until the studio's membership register manages them; removing a
# name here doesn't take anyone out of the group.
studio_members="metaversig"
occ group:info studio >/dev/null 2>&1 || run "studio group" occ group:add studio
for member in $studio_members; do
    run "studio member ${member}" occ group:adduser studio "$member"
done
if occ app:list --enabled --output=json | grep -q '"groupfolders"'; then
    studio_id=$(occ groupfolders:list --output=json 2>/dev/null | php -r '
        foreach ((array) json_decode(stream_get_contents(STDIN), true) as $f) {
            if (($f["mountPoint"] ?? "") === "Studio") { echo $f["id"]; break; }
        }')
    if [ -z "$studio_id" ]; then
        studio_id=$(occ groupfolders:create Studio 2>&1) || {
            echo "==> WARN: studio folder failed: ${studio_id}"
            failures=$((failures + 1))
            studio_id=""
        }
    fi
    if [ -n "$studio_id" ]; then
        run "studio folder access" occ groupfolders:group "$studio_id" studio write share delete
        run "studio folder quota"  occ groupfolders:quota "$studio_id" "200 GB"
    fi
else
    echo "==> WARN: groupfolders isn't enabled, so there's no Studio folder"
    failures=$((failures + 1))
fi

# --- Collabora ------------------------------------------------------------------
# Nextcloud reaches Collabora inside the compose network; browsers use office.*.
run "office url"      occ config:app:set richdocuments wopi_url --value=http://collabora:9980
run "office url"      occ config:app:set richdocuments public_wopi_url --value=https://office.ninjaprivacy.org
run "office config"   occ richdocuments:activate-config

echo "==> settings hook finished with ${failures} warning(s)"
exit 0
