variable "proxmox_endpoint" {
  type    = string
  default = "https://10.0.1.2:8006/"
}

variable "proxmox_insecure" {
  description = "The X-Mansion nodes serve Proxmox's self-signed certificate."
  type        = bool
  default     = true
}

variable "lxc_template" {
  description = "Downloaded to each node by the pve-templates.yml playbook before apply."
  type        = string
  default     = "local:vztmpl/debian-13-standard_13.6-1_amd64.tar.zst"
}

variable "vm_template_vmid" {
  description = "Cloud-init VM template built on the node by pve-templates.yml (role pve_vm_template)."
  type        = number
  default     = 9000
}

variable "gateway" {
  type    = string
  default = "10.0.1.1"
}

variable "dns_servers" {
  type    = list(string)
  default = ["10.0.1.1", "1.1.1.1"]
}
