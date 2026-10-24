variable "hostname" {
  description = "VM name (also the Ansible inventory name)."
  type        = string
}

variable "node" {
  description = "Proxmox node the VM lives on. The template must exist on that node."
  type        = string
}

variable "vmid" {
  type = number
}

variable "template_vmid" {
  description = "VMID of the cloud-init template built by pve-templates.yml. Only used at creation."
  type        = number
}

variable "pool" {
  description = "Proxmox pool. The IaC token only has rights inside this pool."
  type        = string
  default     = "iac"
}

variable "description" {
  type    = string
  default = ""
}

variable "cores" {
  type = number
}

variable "memory" {
  description = "RAM in MiB."
  type        = number
}

variable "disk" {
  description = "Boot disk size in GB (grown from the template's)."
  type        = number
}

variable "datastore" {
  type    = string
  default = "local-lvm"
}

variable "bridge" {
  type    = string
  default = "vmbr0"
}

variable "ip" {
  type = string
}

variable "prefix" {
  type    = number
  default = 16
}

variable "gateway" {
  type = string
}

variable "dns_servers" {
  type = list(string)
}

variable "admin_user" {
  description = "User cloud-init creates, with sudo; the same user the base role manages."
  type        = string
  default     = "ansible"
}

variable "ssh_public_keys" {
  description = "Keys for admin_user at creation (the operators' keys, iac/keys)."
  type        = list(string)
}

variable "start_on_boot" {
  type    = bool
  default = true
}
