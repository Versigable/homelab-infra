output "guests" {
  description = "Read by iac.sh inventory to write the Ansible guest inventory."
  value = merge(
    {
      for name, g in local.lxc_guests : name => {
        kind   = "lxc"
        ip     = module.lxc[name].ip
        vmid   = module.lxc[name].vmid
        node   = module.lxc[name].node
        groups = g.groups
        tags   = sort(distinct(concat(["iac"], g.tags)))
      }
    },
    {
      for name, g in local.vm_guests : name => {
        kind   = "vm"
        ip     = module.vm[name].ip
        vmid   = module.vm[name].vmid
        node   = module.vm[name].node
        groups = g.groups
        tags   = sort(distinct(concat(["iac"], g.tags)))
      }
    },
    # The studio's guests (studio.tf).
    local.studio_inventory,
  )
}
