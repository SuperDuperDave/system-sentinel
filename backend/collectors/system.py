from .base import BaseCollector, CollectorResult
from services.powershell import run_powershell

class SystemCollector(BaseCollector):
    def __init__(self):
        super().__init__(
            collector_id="system.snapshot",
            name="System Hardware Snapshot",
            description="Collects CPU, RAM, BIOS, and OS details"
        )

    def collect(self) -> CollectorResult:
        # PowerShell script to gather info
        ps_script = """
        $os = Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, BuildNumber, OSArchitecture, @{Name='LastBootUpTime'; Expression={$_.LastBootUpTime.ToString('o')}}
        $bios = Get-CimInstance Win32_BIOS | Select-Object Manufacturer, Version, @{Name='ReleaseDate'; Expression={$_.ReleaseDate.ToString('yyyy-MM-dd')}}, SerialNumber
        $cpu = Get-CimInstance Win32_Processor | Select-Object Name, MaxClockSpeed, NumberOfCores
        $ram_obj = Get-CimInstance Win32_PhysicalMemory | Measure-Object -Property Capacity -Sum
        $ram_gb = [math]::Round($ram_obj.Sum / 1GB, 2)
        
        @{
            OS = $os
            BIOS = $bios
            CPU = $cpu
            RAM_GB = $ram_gb
        } | ConvertTo-Json -Compress
        """
        
        data = run_powershell(ps_script)
        
        # Normalize if it comes back as a single dict (it should)
        return CollectorResult(self.collector_id, data)
