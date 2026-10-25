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

variable "cpu_type" {
  description = "CPU model. The default runs on every node, so the VM can migrate; a VM with a passed-through device can't migrate anyway and can use host."
  type        = string
  default     = "x86-64-v2-AES"
}

variable "cpu_units" {
  description = "CPU weight against other guests (null: the Proxmox default)."
  type        = number
  default     = null
}

variable "cpu_affinity" {
  description = "Host CPUs the VM may run on, e.g. \"8-15,24-31\" (null: any)."
  type        = string
  default     = null
}

variable "machine" {
  description = "QEMU machine type; PCIe passthrough needs q35 (null: the Proxmox default)."
  type        = string
  default     = null
}

variable "bios" {
  description = "seabios or ovmf (ovmf also needs efi_disk)."
  type        = string
  default     = "seabios"
}

variable "efi_disk" {
  description = "Add the EFI vars disk that OVMF needs."
  type        = bool
  default     = false
}

variable "hostpci" {
  description = "PCI devices passed through by resource mapping (pve_pci_mappings in Ansible's proxmox_nodes vars)."
  type = list(object({
    device  = string
    mapping = string
    pcie    = optional(bool, true)
    rombar  = optional(bool, true)
  }))
  default = []
}

variable "nic_mtu" {
  description = "MTU of the first NIC (virtio). 1 = take the bridge's, which VXLAN vnets need (1450); null = Proxmox default."
  type        = number
  default     = null
}

variable "extra_nics" {
  description = "NICs after the first, in order, each with a static address and no gateway. mtu as for nic_mtu."
  type = list(object({
    bridge = string
    ip     = string
    prefix = number
    mtu    = optional(number)
  }))
  default = []
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
