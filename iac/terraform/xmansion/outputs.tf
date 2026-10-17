output "guests" {
  description = "Read by iac.sh inventory to write the Ansible guest inventory."
  value = {
    for name, g in local.lxc_guests : name => {
      ip     = module.lxc[name].ip
      vmid   = module.lxc[name].vmid
      node   = module.lxc[name].node
      groups = g.groups
    }
  }
}
