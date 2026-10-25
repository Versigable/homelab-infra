# Wolverine is the cluster's third node (MetaVersig's house), so this site goes through the
# same cluster API, token and pool as xmansion. Its own endpoint wouldn't make the site
# independent: Terraform state lives on X-Mansion's GitLab either way.
variable "proxmox_endpoint" {
  type    = string
  default = "https://10.0.1.2:8006/"
}

variable "proxmox_insecure" {
  description = "The cluster nodes serve Proxmox's self-signed certificate."
  type        = bool
  default     = true
}

variable "lxc_template" {
  description = "Downloaded to each node by the pve-templates.yml playbook before apply. Same as xmansion."
  type        = string
  default     = "local:vztmpl/debian-13-standard_13.6-1_amd64.tar.zst"
}

# Guests sit on LAB (10.1.0.0/16, wolverine's vmbr1). HOME (vmbr0, 192.168.0.0/24) is the
# house network and gets no guests. pfSense (VM 300) is LAB's gateway and resolver.
variable "bridge" {
  type    = string
  default = "vmbr1"
}

variable "gateway" {
  type    = string
  default = "10.1.0.1"
}

variable "dns_servers" {
  type    = list(string)
  default = ["10.1.0.1", "1.1.1.1"]
}
