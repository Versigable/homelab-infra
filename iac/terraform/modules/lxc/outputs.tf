output "vmid" {
  value = proxmox_virtual_environment_container.this.vm_id
}

output "ip" {
  value = var.ip
}

output "node" {
  value = var.node
}
