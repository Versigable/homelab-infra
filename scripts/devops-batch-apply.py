#!/usr/bin/env python3

import sys, os, subprocess, shlex, re, json, tempfile

def load_inventory(path):
    # Try PyYAML if available; else allow JSON; else minimal YAML subset.
    try:
        import yaml  # type: ignore
        with open(path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except Exception:
        # minimal parser for our fixed shape
        txt = open(path, 'r', encoding='utf-8').read()
        if txt.lstrip().startswith('{'):
            return json.loads(txt)
        # naive parse: only handles our structure
        data = {"hosts": {}}
        current_host = None
        current_key = None
        for line in txt.splitlines():
            line = line.rstrip()
            if not line or line.strip().startswith('#'):
                continue
            m = re.match(r'\s{2}([A-Za-z0-9_-]+):\s*$', line)
            if m and current_host is None:
                # start hosts: ... must already be seen
                if m.group(1) == "hosts":
                    continue
            m = re.match(r'\s{2}([A-Za-z0-9_-]+):\s*$', line)
            if m and "hosts:" in txt and line.strip().endswith(':') and not line.strip().startswith('-'):
                # host key at 2 spaces
                if len(line) - len(line.lstrip(' ')) == 2:
                    current_host = m.group(1)
                    data["hosts"][current_host] = {"repos": []}
                    continue
            if current_host:
                m = re.match(r'\s{4}ssh:\s*(.+)$', line)
                if m:
                    data["hosts"][current_host]["ssh"] = m.group(1).strip()
                    continue
                if line.strip().startswith('- '):
                    # new repo item
                    data["hosts"][current_host]["repos"].append({})
                    continue
                if re.match(r'\s{8}path:\s*(.+)$', line):
                    val = line.split(':',1)[1].strip()
                    data["hosts"][current_host]["repos"][-1]["path"] = val
                    continue
                if re.match(r'\s{8}home:\s*(.+)$', line):
                    val = line.split(':',1)[1].strip()
                    data["hosts"][current_host]["repos"][-1]["home"] = val
                    continue
        return data

def run(cmd):
    print(f"→ {cmd}")
    return subprocess.run(cmd, shell=True, check=True)

def main():
    if len(sys.argv) < 2:
        print("Usage: devops-batch-apply.py <inventory.yaml> [--dry-run] [--install-only]")
        sys.exit(1)
    inv_path = sys.argv[1]
    dry = "--dry-run" in sys.argv
    install_only = "--install-only" in sys.argv

    inv = load_inventory(inv_path)
    hosts = inv.get("hosts", {})
    if not hosts:
        print("No hosts in inventory.")
        sys.exit(2)

    # locate scripts directory relative to this script
    here = os.path.dirname(os.path.abspath(__file__))
    scripts_dir = os.path.join(os.path.dirname(here), "scripts")
    grant = os.path.join(scripts_dir, "devops-grant.sh")
    revoke = os.path.join(scripts_dir, "devops-revoke.sh")
    audit = os.path.join(scripts_dir, "devops-audit.sh")
    for p in (grant, revoke, audit):
        if not os.path.exists(p):
            print(f"Missing script: {p}")
            sys.exit(3)

    for name, meta in hosts.items():
        alias = meta.get("ssh", name)
        print(f"\n=== Host: {name} ({alias}) ===")
        # copy scripts
        for local in (grant, revoke, audit):
            remote = f"{alias}:/tmp/{os.path.basename(local)}"
            cmd = f"scp -q {shlex.quote(local)} {remote}"
            if dry:
                print(f"[dry] {cmd}")
            else:
                run(cmd)

        # install scripts
        install_cmd = (
            "sudo install -m 0755 /tmp/devops-grant.sh  /usr/local/sbin/devops-grant.sh && "
            "sudo install -m 0755 /tmp/devops-revoke.sh /usr/local/sbin/devops-revoke.sh && "
            "sudo install -m 0755 /tmp/devops-audit.sh  /usr/local/sbin/devops-audit.sh"
        )
        if dry:
            print(f"[dry] ssh {alias} {install_cmd}")
        else:
            run(f"ssh {alias} {shlex.quote(install_cmd)}")

        if install_only:
            continue

        # grant for each repo
        for repo in meta.get("repos", []):
            path = repo.get("path")
            home = repo.get("home")
            if not path or not home:
                print(f"  ! skipping invalid repo entry: {repo}")
                continue
            cmd = f"sudo /usr/local/sbin/devops-grant.sh {shlex.quote(path)} {shlex.quote(home)}"
            if dry:
                print(f"[dry] ssh {alias} {cmd}")
            else:
                run(f"ssh {alias} {shlex.quote(cmd)}")
                # quick audit head
                run(f"ssh {alias} 'sudo /usr/local/sbin/devops-audit.sh {shlex.quote(path)}'")

    print("\nAll done ✔")

if __name__ == '__main__':
    main()
