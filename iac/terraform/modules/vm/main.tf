# A VM cloned from the cloud-init template that pve-templates.yml builds on the node.
#
# A VM has its own kernel, so a guest that writes to TrueNAS over NFS keeps its dirty pages in
# guest RAM. A node-side NFS client (an LXC's bind mount) can deadlock TrueNAS, which runs on
# the same node (2026-10-23). Guests that write to TrueNAS are VMs for that reason.
#
# cloud-init creates the ansible user with the operators' keys (iac/keys), so a VM needs no root bootstrap:
# site.yml reaches it directly. Tags are applied by Ansible (pve_vm_rootonly), for the same
# pool-permission reason as for containers (see modules/lxc).
resource "proxmox_virtual_environment_vm" "this" {
  node_name   = var.node
  vm_id       = var.vmid
  pool_id     = var.pool
  name        = var.hostname
  description = var.description

  on_boot = var.start_on_boot
  started = true

  clone {
    vm_id        = var.template_vmid
    full         = true
    datastore_id = var.datastore
  }

  # No guest agent yet: Terraform would wait for it to report addresses, and the cloud image
  # doesn't ship it. Turning it on later needs the package in the guest and a VM restart.
  agent {
    enabled = false
  }

  cpu {
    cores = var.cores
    # The Proxmox default model; runs on every node, so the VM can migrate.
    type = "x86-64-v2-AES"
  }

  memory {
    dedicated = var.memory
    # 0 = no balloon device: the VM's RAM is fixed, never reclaimed by the host.
    floating = 0
  }

  disk {
    datastore_id = var.datastore
    interface    = "scsi0"
    size         = var.disk
    iothread     = true
    discard      = "on"
  }

  network_device {
    bridge   = var.bridge
    model    = "virtio"
    firewall = true
  }

  operating_system {
    type = "l26"
  }

  initialization {
    datastore_id = var.datastore

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
      username = var.admin_user
      keys     = var.ssh_public_keys
    }
  }

  lifecycle {
    ignore_changes = [
      # Owned by Ansible (pve_vm_rootonly).
      tags,
      # Creation-time inputs. A new template or a rotated key must not replace a running VM;
      # Ansible manages access afterwards.
      clone,
      initialization[0].user_account,
    ]
  }
}
