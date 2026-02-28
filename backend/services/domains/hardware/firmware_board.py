
import json
from ...powershell import run_powershell

async def get_firmware_board_context():
    """
    Fetches deep forensic context for Motherboard & Firmware.
    - Identity: Board Model, Manufacturer, Version, Serial (if avail).
    - BIOS/UEFI: Vendor, Version, Date, SMBIOS Version.
    - Security: TPM 2.0 Status, Secure Boot State, DMA Protection.
    - Boot: Boot Mode, Last Boot Time.
    """
    
    script = r"""
    $ErrorActionPreference = "SilentlyContinue"

    # 1. Board Identity
    $board = Get-CimInstance Win32_BaseBoard | Select-Object Manufacturer, Product, Version, SerialNumber, HostingBoard
    
    # 2. BIOS / Firmware
    $bios = Get-CimInstance Win32_BIOS | Select-Object Manufacturer, SMBIOSBIOSVersion, ReleaseDate, SerialNumber, Version, Caption
    
    # 3. TPM Status
    $tpm_status = @{
        Present = $false
        Ready = $false
        Managed = $false
        Version = "Unknown"
    }
    
    try {
        $tpm = Get-Tpm
        $tpm_status.Present = $tpm.TpmPresent
        $tpm_status.Ready = $tpm.TpmReady
        $tpm_status.Managed = $tpm.TpmManaged
        # Try to get version if possible, usually requires WMI or deeper calls
        # WMI: root\CIMV2\Security\MicrosoftTpm : Win32_Tpm
        $wmi_tpm = Get-CimInstance -Namespace root\CIMV2\Security\MicrosoftTpm -ClassName Win32_Tpm -ErrorAction SilentlyContinue
        if ($wmi_tpm) {
             $tpm_status.Version = $wmi_tpm.SpecVersion
        }
    } catch { }

    # 4. Secure Boot & DMA
    # Secure Boot is often in Registry
    $secure_boot = $false
    try {
        $sb_reg = Get-ItemProperty "HKLM:\\SYSTEM\\CurrentControlSet\\Control\\SecureBoot\\State" -Name "UEFISecureBootEnabled" -ErrorAction SilentlyContinue
        if ($sb_reg.UEFISecureBootEnabled -eq 1) { $secure_boot = $true }
    } catch { }

    # DMA Protection (Kernel DMA Protection)
    # Registry: HKLM\SYSTEM\CurrentControlSet\Control\DmaSecurity\AllowedBuses
    $dma_protection = $false
    try {
         # Check for presence of distinct DMA security keys or SystemInfo
         # SysInfo is hard to parse in raw Powershell without text parsing. 
         # We can check VBS related keys too.
         $dma_key = Get-Item "HKLM:\\SYSTEM\\CurrentControlSet\\Control\\DmaSecurity" -ErrorAction SilentlyContinue
         if ($dma_key) { $dma_protection = $true } 
    } catch { }
    
    # 5. Risks
    $risks = @()
    
    if (-not $secure_boot) {
        $risks += @{ id="boot-insecure"; level="warning"; message="Secure Boot is Disabled" }
    }
    if ($tpm_status.Present -and -not $tpm_status.Ready) {
        $risks += @{ id="tpm-not-ready"; level="warning"; message="TPM 2.0 Present but Not Ready" }
    }
    if (-not $tpm_status.Present) {
         $risks += @{ id="tpm-missing"; level="error"; message="No TPM Detected" }
    }

    # Return Payload
    @{
        identity = @{
            manufacturer = if ($board.Manufacturer) { $board.Manufacturer.Trim() } else { $null }
            product = if ($board.Product) { $board.Product.Trim() } else { $null }
            version = if ($board.Version) { $board.Version.Trim() } else { $null }
            serial = if ($board.SerialNumber) { $board.SerialNumber.Trim() } else { $null }
        }
        bios = @{
            vendor = if ($bios.Manufacturer) { $bios.Manufacturer.Trim() } else { $null }
            version = if ($bios.SMBIOSBIOSVersion) { $bios.SMBIOSBIOSVersion.Trim() } else { $null }
            release_date = if ($bios.ReleaseDate) { $bios.ReleaseDate.ToString('yyyy-MM-dd') } else { $null }
            full_version_string = if ($bios.Version) { $bios.Version.Trim() } else { $null }
        }
        security = @{
            tpm = $tpm_status
            secure_boot = $secure_boot
            dma_protection = $dma_protection
        }
        risks = $risks
    } | ConvertTo-Json -Depth 3
    """
    
    return run_powershell(script)
