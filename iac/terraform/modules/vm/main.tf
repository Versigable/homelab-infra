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

  # Defaults (null machine, SeaBIOS) are what every VM had before these became variables.
  # GPU passthrough needs q35 and OVMF (see the studio Forge in xmansion/studio.tf).
  machine = var.machine
  bios    = var.bios

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
    # Default: the Proxmox default model, which runs on every node, so the VM can migrate.
    type     = var.cpu_type
    units    = var.cpu_units
    affinity = var.cpu_affinity
  }

  # Secure Boot keys are not enrolled, so unsigned DKMS modules (NVIDIA's) load.
  dynamic "efi_disk" {
    for_each = var.efi_disk ? [1] : []
    content {
      datastore_id      = var.datastore
      file_format       = "raw"
      type              = "4m"
      pre_enrolled_keys = false
    }
  }

  # By resource mapping only: the IaC token may use a mapping (Mapping.Use, granted per
  # mapping by pve-cluster.yml) but can't pass raw PCI ids, which Proxmox keeps for root.
  dynamic "hostpci" {
    for_each = var.hostpci
    content {
      device  = hostpci.value.device
      mapping = hostpci.value.mapping
      pcie    = hostpci.value.pcie
      rombar  = hostpci.value.rombar
    }
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
    mtu      = var.nic_mtu
  }

  # Second and later NICs, in order (a router such as studio-gw has one per network).
  dynamic "network_device" {
    for_each = var.extra_nics
    content {
      bridge   = network_device.value.bridge
      model    = "virtio"
      firewall = true
      mtu      = network_device.value.mtu
    }
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

    # One per extra NIC, same order; no gateway (the first NIC's is the default route).
    dynamic "ip_config" {
      for_each = var.extra_nics
      content {
        ipv4 {
          address = "${ip_config.value.ip}/${ip_config.value.prefix}"
        }
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
