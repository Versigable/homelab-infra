# SOPS + age Secrets Playbook

## Convention

- Encrypted dotenv files end in `*.env.sops`
- Stored under `hosts/<host>/secrets/`
- Plain `.env` exists only on the target host

---

## Correct Decrypt Command

```
sops -d --input-type dotenv --output-type dotenv file.env.sops
```

---

## Safe Deploy Pattern

Use subshell + temp file + EXIT trap:

```
(
  umask 077
  TMP_ENV="$(mktemp)"
  trap 'rm -f "${TMP_ENV}"' EXIT

  sops -d --input-type dotenv --output-type dotenv "$SOPS_ENV" > "$TMP_ENV"
  sudo install -m 0600 -o root -g root "$TMP_ENV" "$LIVE_ENV"
)
```

---

## Host Key Location

```
/etc/sops/age/keys.txt
```

Permissions:
- root:gitlab-runner 0640
