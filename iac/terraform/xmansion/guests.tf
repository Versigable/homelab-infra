locals {
  # Every Terraform-managed LXC on X-Mansion. Ansible's guest inventory is generated
  # from the `guests` output (iac.sh inventory), so this is the only list to edit.
  # groups: Ansible groups the guest joins, which pick its playbook roles.
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
  }
}

module "lxc" {
  source   = "../modules/lxc"
  for_each = local.lxc_guests

  hostname        = each.key
  node            = each.value.node
  vmid            = each.value.vmid
  description     = each.value.description
  tags            = each.value.tags
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
