import logging
import json
from typing import Dict, Any, List, Optional
from services.powershell import run_powershell

logger = logging.getLogger(__name__)

async def get_pcie_fabric_context() -> Dict[str, Any]:
    """
    Retrieves the PCIe Fabric Physics Map.
    - Raw Topology (Roots, Endpoints)
    - Derived: Shared Fabric Groups (Endpoints sharing a Root Port)
    - Derived: Path Addressability (BDF, Upstream Chain)
    """
    try:
        script = _get_pcie_fabric_script()
        data = run_powershell(script)
        
        if data and isinstance(data, dict):
            # Ensure "derived" structure exists if PS didn't create it fully
            if "derived" not in data:
                data["derived"] = {}
            return data
            
        return {"error": "Invalid data returned from PCIe service", "raw_data": str(data)}
        
    except Exception as e:
        logger.error(f"PCIe Fabric context fetch failed: {e}")
        return {"error": str(e)}

def _get_pcie_fabric_script() -> str:
    return r"""
    $ErrorActionPreference = "SilentlyContinue"
    
    # 1. Fetch All Present Devices
    $all_devs = Get-PnpDevice -PresentOnly
    
    # 2. Build Lookup Maps
    $dev_map = @{}
    foreach ($d in $all_devs) {
        if ($d.InstanceId) { $dev_map[$d.InstanceId] = $d }
    }
    
    # 3. Helper: Walk Upstream Chain
    function Get-UpstreamChain ($instanceId) {
        $chain = @()
        $currentId = $instanceId
        
        while ($currentId -and $dev_map.ContainsKey($currentId)) {
            $dev = $dev_map[$currentId]
            $chain += $dev.InstanceId
            
            # Stop if we hit root (ACPI, or no parent)
            if ($dev.InstanceId -like "ACPI\*") { break }
            
            # Get Parent
            $parentProp = Get-PnpDeviceProperty -InstanceId $dev.InstanceId -KeyName "DEVPKEY_Device_Parent" -ErrorAction SilentlyContinue
            if ($parentProp -and $parentProp.Data) {
                $currentId = $parentProp.Data
            } else {
                break
            }
        }
        return $chain
    }

    # 4. Filter for PCIe Roots and Endpoints
    $pci_devs = $all_devs | Where-Object { $_.InstanceId -like "PCI\*" }
    
    # Roots: Usually "Root Port" or "PCI Bridge" in name, or specific Class
    $roots = $pci_devs | Where-Object { $_.Class -eq "System" -and ($_.FriendlyName -like "*Root*" -or $_.FriendlyName -like "*Bridge*") }
    
    # Endpoints: The devices we care about (Storage, Net, GPU, USB)
    # We broaden the filter to catch anything usually hung off PCIe
    $endpoints = $pci_devs | Where-Object { $_.Class -in @("Display", "SCSIAdapter", "Net", "MEDIA", "USB", "System") -and $_.FriendlyName -notlike "*Root*" -and $_.FriendlyName -notlike "*Bridge*" }

    $endpoint_objects = @()
    $root_objects = @()
    
    # 5. Process Endpoints & Build Physics Map
    foreach ($ep in $endpoints) {
        # Raw Props
        $locPaths = Get-PnpDeviceProperty -InstanceId $ep.InstanceId -KeyName "DEVPKEY_Device_LocationPaths"
        $svc = Get-PnpDeviceProperty -InstanceId $ep.InstanceId -KeyName "DEVPKEY_Device_Service"
        
        # BDF Extraction (Best Effort from LocationPaths)
        # Format: PCIROOT(0)#PCI(0100)#PCI(0000) -> 01:00.0 (Bus 1, Dev 0, Func 0)
        # This is complex in pure regex, we'll store the LocationPath verbatim for now as the "Physics Address"
        
        # Upstream Chain
        $chain = Get-UpstreamChain $ep.InstanceId
        
        # Find the "Root Port" ancestor (The bridge closest to CPU/Chipset)
        # Usually the last "PCI\VEN..." item before ACPI, or specific Root Port class
        $rootPortId = $null
        foreach ($id in $chain) {
            $d = $dev_map[$id]
            if ($d.FriendlyName -like "*Root*" -or $d.FriendlyName -like "*Bridge*") {
                $rootPortId = $id
            }
        }
        
        # Status & Prob Code
        $prob = Get-PnpDeviceProperty -InstanceId $ep.InstanceId -KeyName "DEVPKEY_Device_ProblemCode"

        $endpoint_objects += @{
            name = $ep.FriendlyName
            instance_id = $ep.InstanceId
            class = $ep.Class
            service = if ($svc.Data) { $svc.Data } else { "Unknown" }
            location_paths = if ($locPaths.Data) { $locPaths.Data } else { @() }
            upstream_chain = $chain
            root_port_id = $rootPortId
            status = $ep.Status
            cm_prob = if ($prob.Data) { $prob.Data } else { 0 }
        }
    }

    # 6. Process Roots
    foreach ($r in $roots) {
        $root_objects += @{
            name = $r.FriendlyName
            instance_id = $r.InstanceId
            status = $r.Status
        }
    }

    # 7. Derived: Shared Fabric Groups
    # Group Endpoints by their RootPortID
    $groups = @{}
    foreach ($ep in $endpoint_objects) {
        $rid = $ep.root_port_id
        if ($rid) {
            if (-not $groups.ContainsKey($rid)) { $groups[$rid] = @() }
             # Store object for frontend interactivity
            $groups[$rid] += @{
                name = $ep.name
                id = $ep.instance_id
            }
        }
    }
    
    $shared_groups = @()
    foreach ($key in $groups.Keys) {
        $members = $groups[$key]
        if ($members.Count -gt 1) {
            $rootName = if ($dev_map.ContainsKey($key)) { $dev_map[$key].FriendlyName } else { $key }
            $shared_groups += @{
                root_port_id = $key
                root_name = $rootName
                members = $members
            }
        }
    }

    # Final Payload
    @{
        raw = @{
            roots = $root_objects
            endpoints = $endpoint_objects
        }
        derived = @{
            shared_groups = $shared_groups
        }
    } | ConvertTo-Json -Depth 8
    """
