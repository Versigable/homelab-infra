# cf-tunnel: cloudflared replicas

LXC 117 `cf-tunnel-1` (phoenix, 10.0.1.17, runner 18) and LXC 118 `cf-tunnel-2` (rogue,
10.0.1.18, runner 19), built by `iac/`. Each runs a replica of both Cloudflare tunnels, so
WARP and the Proxmox breakglass path survive either node going down.

| Service | Tunnel | Managed | Secret | Metrics / ready |
|---|---|---|---|---|
| `cf-org` | `ninjaprivacy_org` (37111c6c-…), public sites + WARP | locally: `config/cloudflared-org.yml` | `secrets/org.env.sops` | `127.0.0.1:20241/ready` |
| `cf-us` | `ninjaprivacy_us`, "tunnel B", Proxmox breakglass | Zero Trust dashboard | `secrets/us.env.sops` | `127.0.0.1:20242/ready` |

The Traefik VM runs the third `ninjaprivacy_org` replica (`hosts/traefik`). Keep
`config/cloudflared-org.yml` in step with `hosts/traefik/data/cloudflared/config.yml`, and
both images on the same pinned release.

`scripts/deploy.sh` starts a service only when its secret exists and removes it when the
secret goes away, so either tunnel can be added or withdrawn on its own.

## Secrets

Each file is a SOPS dotenv with one line, `TUNNEL_TOKEN=<token>`, encrypted to both guests'
age keys (`.sops.yaml`). The token is never printed or pasted anywhere: it goes straight
from its source into `sops`. The encrypted file is safe to copy and commit.

Both guests' public keys, for the commands below:

```
K=age1qte9y0lys5crs9vxhg86q583mclhqz9kw0j6k6y8shdy25cw74ascmutzn,age184v8exz6uyhsxdwhfhlgc2c2sxtmwpqpg3caqxup75l8val4agcq865kl7
```

### `org.env.sops` (ninjaprivacy_org)

On the Traefik VM (it has the tunnel's origin cert, and `sops`), as root:

```bash
K=age1qte9y0lys5crs9vxhg86q583mclhqz9kw0j6k6y8shdy25cw74ascmutzn,age184v8exz6uyhsxdwhfhlgc2c2sxtmwpqpg3caqxup75l8val4agcq865kl7
umask 077; set -o pipefail
docker exec -u 0 ninjaprivacy_org cloudflared tunnel --origincert /etc/cloudflared/cert.pem \
    token 37111c6c-064b-4381-9065-9313b50dffb3 \
  | sed 's/^/TUNNEL_TOKEN=/' \
  | sops --encrypt --age "$K" --input-type dotenv --output-type dotenv /dev/stdin > /root/org.env.sops
grep -c '^TUNNEL_TOKEN=ENC\[' /root/org.env.sops    # expect 1
```

### `us.env.sops` (ninjaprivacy_us)

Zero Trust → Networks → Tunnels → `ninjaprivacy_us` → Configure: the install command
shows the token (the long string after `--token` / `install`). Rotating it ("Refresh
token") disconnects every connector still on the old one. On any host with `sops` (for
example LXC 117, `pct exec 117 -- bash` on phoenix, as root):

```bash
K=age1qte9y0lys5crs9vxhg86q583mclhqz9kw0j6k6y8shdy25cw74ascmutzn,age184v8exz6uyhsxdwhfhlgc2c2sxtmwpqpg3caqxup75l8val4agcq865kl7
umask 077; set -o pipefail
read -rs TOK && printf 'TUNNEL_TOKEN=%s\n' "$TOK" \
  | sops --encrypt --age "$K" --input-type dotenv --output-type dotenv /dev/stdin > /root/us.env.sops
unset TOK
grep -c '^TUNNEL_TOKEN=ENC\[' /root/us.env.sops     # expect 1
```

(`read -rs` reads the pasted token without echoing it.)

Commit the encrypted file as `hosts/cf-tunnel/secrets/<name>.env.sops`; the merge deploys it.

### After rebuilding a guest

`iac/` gives a rebuilt guest a new age key. Put it in `.sops.yaml` and in `K` above, then
re-encrypt both files: `sops updatekeys` on the other guest (it can still decrypt them), or
make them again from their tokens.

## Checks

On either guest: `curl -s 127.0.0.1:20241/ready` (and `:20242`) returns
`{"status":200,"readyConnections":4,...}` when the replica is connected.
`docker logs cf-org` shows `Registered tunnel connection` four times.
