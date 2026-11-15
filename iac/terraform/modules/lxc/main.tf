# An unprivileged LXC, created through the Proxmox API with the iac@pve!infra token.
#
# Proxmox only lets root@pam change some container settings: feature flags other than
# nesting (keyctl, fuse, mount) and bind mounts. Those are applied on the node by the
# Ansible role pve_lxc_rootonly, so Terraform ignores them after creation instead of
# fighting Ansible over them.
#
# Tags go the same way. Proxmox checks tag rights on /vms/<id> without counting the pool
# (GuestHelpers::assert_tag_permissions), and a new guest isn't in the pool yet, so a
# pool-scoped token can't create a tagged guest. A per-guest ACL would be deleted with
# the guest, which breaks rebuilds.
resource "proxmox_virtual_environment_container" "this" {
  node_name   = var.node
  vm_id       = var.vmid
  pool_id     = var.pool
  description = var.description

  unprivileged  = true
  start_on_boot = var.start_on_boot
  started       = true

  features {
    nesting = true
  }

  cpu {
    cores = var.cores
  }

  memory {
    dedicated = var.memory
    swap      = var.swap
  }

  disk {
    datastore_id = var.datastore
    size         = var.disk
  }

  network_interface {
    name        = "eth0"
    bridge      = var.bridge
    firewall    = true
    mac_address = var.mac_address
  }

  operating_system {
    template_file_id = var.template
    type             = var.os_type
  }

  initialization {
    hostname = var.hostname

    ip_config {
      ipv4 {
        address = "${var.ip}/${var.prefix}"
        gateway = var.gateway
      }
    }

    dns {
      servers = var.dns_servers
    }

    user_account {
      keys = var.ssh_public_keys
    }
  }

  lifecycle {
    ignore_changes = [
      # Settings owned by Ansible (pve_lxc_rootonly).
      features,
      mount_point,
      tags,
      hook_script_file_id,
      # Creation-time inputs. A new template or a rotated bootstrap key must not
      # replace a running container; Ansible manages access after bootstrap.
      operating_system[0].template_file_id,
      initialization[0].user_account,
    ]
  }
}
