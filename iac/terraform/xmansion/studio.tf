# The game studio's guests (vault: 1-Projects/game-studio-platform/design/).
#
# They live on the studio network: VNet studio0 in the Proxmox SDN zone "studio" (VXLAN,
# phoenix + rogue), created by ansible/playbooks/pve-cluster.yml before `tf apply`. Nothing
# on studio0 has a route to vmbr0; studio-gw is the network's only way in or out.
#
# Studio VMIDs are 150-169; studio0 addresses are 10.77.0.0/24.
#   150 studio-gw     phoenix  router: egress allow-list, DNS, NTP, ingress for play streams
#   155 studio-forge  phoenix  the GPU VM; holds the RTX 5070 Ti for good
# Later: 151 studio-hub, 160/161 the cells (rogue), once rogue has a VM template.
locals {
  studio_net = {
    bridge  = "studio0"
    prefix  = 24
    gateway = "10.77.0.1"
  }

  # Inventory entries for the `guests` output (outputs.tf), read by `iac.sh inventory`.
  studio_inventory = {
    "studio-gw" = {
      kind   = "vm"
      ip     = module.studio_gw.ip
      vmid   = module.studio_gw.vmid
      node   = module.studio_gw.node
      groups = ["studio_gw"]
      tags   = ["iac", "studio"]
    }
    "studio-forge" = {
      kind   = "vm"
      ip     = module.studio_forge.ip
      vmid   = module.studio_forge.vmid
      node   = module.studio_forge.node
      groups = ["docker_hosts", "studio_forge", "studio_private"]
      tags   = ["gpu", "iac", "studio"]
    }
  }
}

# On phoenix with the Forge, so play streams go Traefik -> gw -> Forge without leaving the
# node. It holds no credentials and runs no app: nftables, dnsmasq and chrony (role studio_gw).
module "studio_gw" {
  source = "../modules/vm"

  hostname      = "studio-gw"
  node          = "phoenix"
  vmid          = 150
  template_vmid = var.vm_template_vmid
  description   = "Studio network router: egress allow-list, DNS, NTP, play-stream ingress. Ansible role studio_gw. Vault: 1-Projects/game-studio-platform/design/architecture."
  cores         = 1
  memory        = 1024
  disk          = 8

  # net0 on the LAN (default route); net1 is the studio network's gateway address.
  ip          = "10.0.1.150"
  gateway     = var.gateway
  dns_servers = var.dns_servers
  extra_nics = [{
    bridge = local.studio_net.bridge
    ip     = local.studio_net.gateway
    prefix = local.studio_net.prefix
    mtu    = 1
  }]

  ssh_public_keys = local.operator_keys
}

# The GPU VM. Passthrough pins all of its RAM, so it is the one studio guest that can't
# balloon: 16 GiB for the play lane now, 20 GiB when the gen lane lands (one planned restart).
# It is pinned to phoenix's second CCD (CPUs 8-15 and their SMT siblings 24-31), away from
# TrueNAS and GitLab: Ansible sets that (affinity is root-only; host_vars/studio-forge.yml),
# and it takes effect at the VM's next start. q35 + OVMF without Secure Boot keys is the
# recipe jf used on this card.
module "studio_forge" {
  source = "../modules/vm"

  hostname      = "studio-forge"
  node          = "phoenix"
  vmid          = 155
  template_vmid = var.vm_template_vmid
  description   = "Studio Forge: RTX 5070 Ti, lanes gen/play/render/seat. Ansible roles forge_gpu, forge_play. Vault: 1-Projects/game-studio-platform/design/architecture."
  cores         = 8
  memory        = 16384
  disk          = 100

  cpu_type = "host"
  machine  = "q35"
  bios     = "ovmf"
  efi_disk = true
  hostpci = [{
    device  = "hostpci0"
    mapping = "gpu-5070ti"
  }]

  bridge      = local.studio_net.bridge
  nic_mtu     = 1
  ip          = "10.77.0.10"
  prefix      = local.studio_net.prefix
  gateway     = local.studio_net.gateway
  dns_servers = [local.studio_net.gateway]

  ssh_public_keys = local.operator_keys

  # The VNet and the GPU mapping come from pve-cluster.yml, outside Terraform; studio-gw
  # must exist first so the Forge has DNS and a route when it boots.
  depends_on = [module.studio_gw]
}
