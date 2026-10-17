terraform {
  required_version = ">= 1.9"

  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "0.115.0"
    }
  }

  # GitLab-managed state. The address, lock settings and credentials come from the
  # TF_HTTP_* variables that iac/scripts/iac.sh sets; none of it lives in this repo.
  backend "http" {}
}

# The API token comes from PROXMOX_VE_API_TOKEN (iac@pve!infra), set by iac/scripts/iac.sh.
provider "proxmox" {
  endpoint = var.proxmox_endpoint
  insecure = var.proxmox_insecure
}
