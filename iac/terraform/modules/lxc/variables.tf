variable "hostname" {
  description = "Container hostname (also the Ansible inventory name)."
  type        = string
}

variable "node" {
  description = "Proxmox node the container lives on."
  type        = string
}

variable "vmid" {
  type = number
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

variable "swap" {
  description = "Swap in MiB."
  type        = number
  default     = 512
}

variable "disk" {
  description = "Root disk size in GB."
  type        = number
}

variable "datastore" {
  type    = string
  default = "local-lvm"
}

variable "template" {
  description = "LXC template volume, e.g. local:vztmpl/debian-13-standard_13.6-1_amd64.tar.zst. Only used at creation."
  type        = string
}

variable "os_type" {
  type    = string
  default = "debian"
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

variable "ssh_public_keys" {
  description = "Keys for root at creation, so Ansible can bootstrap. Ansible then creates its own user and turns off root SSH."
  type        = list(string)
}

variable "start_on_boot" {
  type    = bool
  default = true
}
