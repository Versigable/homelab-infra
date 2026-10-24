locals {
  # Every operator's public key (iac/keys/*.pub); new guests accept all of them. Ansible's base
  # role keeps the same list on existing guests.
  operator_keys = [for f in sort(fileset("${path.module}/../../keys", "*.pub")) : trimspace(file("${path.module}/../../keys/${f}"))]

  # Every Terraform-managed LXC on X-Mansion. Ansible's guest inventory is generated
  # from the `guests` output (iac.sh inventory), so this is the only list to edit.
  # groups: Ansible groups the guest joins, which pick its playbook roles.
  # tags: Proxmox tags ("iac" is always added); applied by Ansible, see modules/lxc.
  lxc_guests = {
    nextcloud = {
      vmid        = 110
      node        = "phoenix"
      ip          = "10.0.1.10"
      cores       = 4
      memory      = 6144
      swap        = 1024
      disk        = 40
      tags        = ["nextcloud"]
      groups      = ["docker_hosts", "nextcloud"]
      description = "Nextcloud + Collabora. App: homelab-infra hosts/nextcloud (GitOps). Vault: 1-Projects/nextcloud."
    }
    # No USB or Bluetooth passthrough: the Zigbee radio is on the network, so this
    # guest can move between nodes. Not on rogue, which hosts the studio cells.
    "home-assistant" = {
      vmid        = 112
      node        = "phoenix"
      ip          = "10.0.1.12"
      cores       = 2
      memory      = 4096
      swap        = 1024
      disk        = 32
      tags        = ["home-assistant"]
      groups      = ["docker_hosts", "home_assistant"]
      description = "Home Assistant (Container) + Mosquitto + Zigbee2MQTT. App: homelab-infra hosts/home-assistant (GitOps). Vault: 1-Projects/home-assistant."
    }
    # On phoenix, next to TrueNAS, so the music never leaves the host. 111 is reserved
    # for mail-relay; 114 keeps the VMID matching the last octet.
    navidrome = {
      vmid        = 114
      node        = "phoenix"
      ip          = "10.0.1.14"
      cores       = 1
      memory      = 1024
      swap        = 512
      disk        = 16
      tags        = ["navidrome"]
      groups      = ["docker_hosts", "navidrome"]
      description = "Navidrome (private music, 3 accounts). App: homelab-infra hosts/navidrome (GitOps). Vault: 1-Projects/navidrome-music."
    }
  }

  # Every Terraform-managed VM on X-Mansion, cloned from the cloud-init template
  # (var.vm_template_vmid, built by pve-templates.yml). Same fields as lxc_guests, minus swap.
  # Guests that write to TrueNAS over NFS are VMs (vault: 5-Decisions/2026-10-23_nextcloud-vm-instead-of-lxc).
  vm_guests = {
    # Replaces LXC 110 (vault: 1-Projects/nextcloud/notes/vm-migration-plan). Mounts the
    # TrueNAS nextcloud share itself; phoenix never mounts it once the cutover is done.
    "nextcloud-vm" = {
      vmid        = 116
      node        = "phoenix"
      ip          = "10.0.1.16"
      cores       = 4
      memory      = 6144
      disk        = 40
      tags        = ["nextcloud"]
      groups      = ["docker_hosts", "nextcloud"]
      description = "Nextcloud + Collabora (VM, replaces LXC 110). App: homelab-infra hosts/nextcloud (GitOps). Vault: 1-Projects/nextcloud."
    }
  }
}

module "lxc" {
  source   = "../modules/lxc"
  for_each = local.lxc_guests

  hostname        = each.key
  node            = each.value.node
  vmid            = each.value.vmid
  description     = each.value.description
  cores           = each.value.cores
  memory          = each.value.memory
  swap            = each.value.swap
  disk            = each.value.disk
  ip              = each.value.ip
  template        = var.lxc_template
  gateway         = var.gateway
  dns_servers     = var.dns_servers
  ssh_public_keys = local.operator_keys
}

module "vm" {
  source   = "../modules/vm"
  for_each = local.vm_guests

  hostname        = each.key
  node            = each.value.node
  vmid            = each.value.vmid
  template_vmid   = var.vm_template_vmid
  description     = each.value.description
  cores           = each.value.cores
  memory          = each.value.memory
  disk            = each.value.disk
  ip              = each.value.ip
  gateway         = var.gateway
  dns_servers     = var.dns_servers
  ssh_public_keys = local.operator_keys
}
