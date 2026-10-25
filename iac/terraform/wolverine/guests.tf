locals {
  # Every operator's public key (iac/keys/*.pub); new guests accept all of them. Ansible's base
  # role keeps the same list on existing guests.
  operator_keys = [for f in sort(fileset("${path.module}/../../keys", "*.pub")) : trimspace(file("${path.module}/../../keys/${f}"))]

  # Every Terraform-managed LXC on wolverine. Same fields as terraform/xmansion; `iac.sh
  # inventory` (IAC_SITE=wolverine) writes them to ansible/inventory/guests-wolverine.yml.
  # VMIDs are cluster-wide; wolverine's guests use the 30x series (300 pfSense, 301 openclaw,
  # 302 monitoring).
  # mac_address: required here. Both control nodes reach these guests through pfSense, so a
  # rebuild has to keep the MAC (see modules/lxc, variable mac_address). Pick one in Proxmox's
  # BC:24:11 range that no other guest uses.
  lxc_guests = {
    # MetaVersig's own Home Assistant (vault: 1-Projects/home-assistant, phase 1b). Same
    # platform as X-Mansion's (LXC 112), so it joins the same Ansible groups and keeps the
    # same service layout. Its Zigbee radio is on the network (wolverine-sw1 Gi1/0/2, LAB).
    "home-assistant-wolverine" = {
      vmid        = 303
      ip          = "10.1.0.12"
      mac_address = "BC:24:11:AD:81:57"
      cores       = 2
      memory      = 4096
      swap        = 1024
      disk        = 32
      tags        = ["home-assistant"]
      groups      = ["docker_hosts", "home_assistant"]
      description = "Home Assistant (Container) + Mosquitto + Zigbee2MQTT, MetaVersig's house. App: homelab-infra hosts/home-assistant (GitOps). Vault: 1-Projects/home-assistant (phase 1b)."
    }
  }
}

module "lxc" {
  source   = "../modules/lxc"
  for_each = local.lxc_guests

  hostname        = each.key
  node            = "wolverine"
  vmid            = each.value.vmid
  description     = each.value.description
  cores           = each.value.cores
  memory          = each.value.memory
  swap            = each.value.swap
  disk            = each.value.disk
  ip              = each.value.ip
  mac_address     = each.value.mac_address
  bridge          = var.bridge
  template        = var.lxc_template
  gateway         = var.gateway
  dns_servers     = var.dns_servers
  ssh_public_keys = local.operator_keys
}
