# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Compute inventory module: uses aggregated_list to sweep every zone in one call,
printing VMs (machine type, status) and disks. Flags TERMINATED instances that
still have attached disks — stopped VMs stop CPU billing but their disks and
static IPs keep charging.
"""

import os

from google.api_core import exceptions
from google.cloud import compute_v1

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")


def inventory_compute(project_id: str = PROJECT_ID) -> dict:
    """
    Sweeps all zones for Compute Engine instances and disks via aggregated_list.

    Flags:
      - TERMINATED instances with attached disks (disk billing continues)
      - Unattached (orphaned) disks (pure waste)

    Returns a dict with:
      - 'instances': list of (zone, instance) tuples
      - 'disks': list of (zone, disk) tuples
      - 'stopped_with_disks': int count of cost-flagged stopped VMs
    """
    # aggregated_list returns a dict of zone -> scoped list; zones with no
    # resources come back with a 'warning' instead of items. This is the
    # cheap way to inventory compute — one API call vs ~40 per-zone calls.
    instances_client = compute_v1.InstancesClient()
    disks_client = compute_v1.DisksClient()

    print("=" * 80)
    print(f"COMPUTE INVENTORY — project {project_id} (all zones, aggregated_list)")
    print("=" * 80)

    instances, disks = [], []
    try:
        for zone, scoped in instances_client.aggregated_list(project=project_id):
            for inst in scoped.instances:  # empty list when zone has none
                instances.append((zone.rsplit("/", 1)[-1], inst))
        for zone, scoped in disks_client.aggregated_list(project=project_id):
            for disk in scoped.disks:
                disks.append((zone.rsplit("/", 1)[-1], disk))
    except exceptions.PermissionDenied:
        print("Permission denied: needs roles/compute.viewer on the project.")
        return {"instances": [], "disks": [], "stopped_with_disks": 0}
    except exceptions.Forbidden:
        print("Forbidden — is compute.googleapis.com enabled and do you have")
        print("roles/compute.viewer on the project?")
        return {"instances": [], "disks": [], "stopped_with_disks": 0}

    print(f"\nINSTANCES ({len(instances)}):")
    print("-" * 80)
    stopped_with_disks = 0
    for zone, inst in instances:
        machine_type = inst.machine_type.rsplit("/", 1)[-1]
        attached = len(inst.disks)
        flag = ""
        # FinOps: TERMINATED = stopped. CPU/RAM billing stops, but every
        # attached PD keeps billing per-GB, and any reserved static IP on a
        # stopped VM bills MORE than on a running one. Stopped-for-months VMs
        # are classic zombie spend — snapshot + delete, or schedule deletion.
        if inst.status == "TERMINATED" and attached:
            stopped_with_disks += 1
            flag = f"  <-- STOPPED with {attached} attached disk(s) still billing"
        print(f"  {inst.name:<30} {machine_type:<18} {inst.status:<12} zone={zone}{flag}")

    print(f"\nDISKS ({len(disks)}):")
    print("-" * 80)
    for zone, disk in disks:
        users = len(disk.users)  # 0 users = unattached = pure waste
        flag = "  <-- UNATTACHED (orphan — snapshot & delete)" if users == 0 else ""
        dtype = disk.type_.rsplit("/", 1)[-1]
        print(f"  {disk.name:<30} {disk.size_gb:>6} GB  {dtype:<14} "
              f"attached_to={users} zone={zone}{flag}")

    print("\n" + "=" * 80)
    if not instances and not disks:
        print("No compute resources in this project — nothing to optimize here.")
    else:
        print(f"Summary: {len(instances)} instances, {len(disks)} disks, "
              f"{stopped_with_disks} stopped-with-disks cost flag(s).")

    return {
        "instances": instances,
        "disks": disks,
        "stopped_with_disks": stopped_with_disks,
    }
