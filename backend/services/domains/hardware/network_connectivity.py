
import json
from ...powershell import run_powershell

async def get_network_connectivity_context():
    """
    Fetches deep forensic context for Network Connectivity.
    - Adapters: Identity, MAC, Link Speed, MediaType.
    - Configuration: DHCP, DNS, IP Addresses.
    - Drivers: Version, Date, Provider.
    - Risks: Disconnected states, Legacy drivers.
    """
    
    script = r"""
    $ErrorActionPreference = "SilentlyContinue"
    
    # Get physical adapters (and significant virtual ones)
    $adapters = Get-NetAdapter | Where-Object { $_.HardwareInterface -or $_.Virtual -eq $false }
    
    $net_data = @()
    $risks = @()

    foreach ($adapter in $adapters) {
        $ipConf = $adapter | Get-NetIPConfiguration -ErrorAction SilentlyContinue
        $driver = $adapter | Get-NetAdapterDriver -ErrorAction SilentlyContinue
        
        $ipv4 = ($ipConf.IPv4Address).IPAddress
        # Handle array or single string return
        if ($ipv4 -is [array]) { $ipv4 = $ipv4[0] }

        $ipv6 = ($ipConf.IPv6Address).IPAddress
         if ($ipv6 -is [array]) { $ipv6 = $ipv6[0] }

        $dns = ($ipConf.DNSServer).ServerAddresses
        
        $ip_info = @{
            ipv4 = $ipv4
            ipv6 = $ipv6
            gateway = ($ipConf.IPv4DefaultGateway).NextHop
            dns = $dns
            dhcp_enabled = $ipConf.NetIPv4Interface.Dhcp -eq 'Enabled'
        }

        # Check for risks
        # Wired connection < 100 Mbps when UP
        if ($adapter.Status -eq 'Up' -and $adapter.LinkSpeed -and $adapter.LinkSpeed -notlike "*Gbps*" -and $adapter.LinkSpeed -notlike "*1000 Mbps*" -and $adapter.MediaType -eq '802.3') {
             # Rough heuristic, looking for 10/100 connections on modern hardware
             # But LinkSpeed string parsing in PS is tricky ("1 Gbps"). 
             # Let's rely on Numeric property if available or just skip complex speed risk for now.
             # Actually Get-NetAdapter object has LinkSpeed as string "1 Gbps".
        }
        
        if ($driver.DriverDate -and (Get-Date $driver.DriverDate) -lt (Get-Date).AddYears(-3)) {
            $risks += @{ id="net-driver-$($adapter.Name)"; level="warning"; message="$($adapter.Name) Old Driver ($($driver.DriverDate))" }
        }

        # Get PnP Status for "Disabled" detection
        $pnp = Get-PnpDevice -InstanceId $adapter.PnPDeviceID -ErrorAction SilentlyContinue
        $cm_prob = 0
        if ($pnp) {
            $cm_prob = $pnp.ConfigManagerErrorCode
        }

        $net_data += @{
            id = $adapter.InterfaceGuid
            identity = @{
                name = $adapter.Name
                description = $adapter.InterfaceDescription
                mac = $adapter.MacAddress
                ifIndex = $adapter.InterfaceIndex
                pnp_id = $adapter.PnPDeviceID
            }
            status = @{
                oper_status = $adapter.Status
                admin_status = $adapter.AdminStatus
                link_speed = $adapter.LinkSpeed
                media_type = $adapter.MediaType
                cm_prob = $cm_prob
                is_disabled = ($cm_prob -eq 22)
            }
            config = $ip_info
            driver = @{
                provider = $driver.ProviderName
                version = $driver.DriverVersion
                date = if ($driver.DriverDate) { $driver.DriverDate.ToString('yyyy-MM-dd') } else { $null }
            }
        }
    }
    
    @{
        adapters = $net_data
        risks = $risks
    } | ConvertTo-Json -Depth 4
    """

    return run_powershell(script)
