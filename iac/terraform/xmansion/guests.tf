locals {
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
  ssh_public_keys = [trimspace(file(pathexpand(var.ssh_public_key_file)))]
}
