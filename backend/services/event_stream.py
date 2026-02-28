import asyncio
import json
import logging
from datetime import datetime
from services.powershell import run_powershell

logger = logging.getLogger(__name__)

# User-defined presets
PRESETS = [
    {
        "id": "crash_power",
        "logName": "System",
        "providers": ["Microsoft-Windows-Kernel-Power", "EventLog", "Microsoft-Windows-WER-SystemErrorReporting", "volmgr"],
        "eventIds": [41, 6008, 1001, 46, 161, 162]
    },
    {
        "id": "whea_hardware",
        "logName": "System",
        "providers": ["Microsoft-Windows-WHEA-Logger"],
        "eventIds": [1, 17, 18, 19, 20, 46, 47]
    },
    {
        "id": "storage_io",
        "logName": "System",
        "providers": ["Disk", "Ntfs", "storahci", "stornvme", "storport", "iaStorA", "iaStorAC"],
        "eventIds": [7, 11, 51, 55, 57, 129, 153]
    },
    {
        "id": "driver_service",
        "logName": "System",
        "providers": ["Microsoft-Windows-Kernel-PnP", "Microsoft-Windows-DriverFrameworks-UserMode", "Service Control Manager"],
        "eventIds": [219, 10110, 10111, 7000, 7001, 7009, 7011, 7026, 7031, 7034]
    },
    {
        "id": "app_crashes",
        "logName": "Application",
        "providers": ["Application Error", "Windows Error Reporting"],
        "eventIds": [1000, 1001, 1002]
    },
    {
        "id": "tpm_secureboot",
        "logName": "System",
        "providers": ["Microsoft-Windows-TPM-WMI"],
        "eventIds": [1796, 1801]
    }
]

def get_grouped_filters():
    """Groups event IDs by LogName."""
    filters = {}
    for p in PRESETS:
        log = p["logName"]
        if log not in filters:
            filters[log] = set()
        filters[log].update(p["eventIds"])
    return {k: list(v) for k, v in filters.items()}

GROUPED_FILTERS = get_grouped_filters()

class EventStreamGenerator:
    def __init__(self):
        self.cursors = {
            'System': 0,
            'Application': 0
        }
        # Initialize cursors to 'now' roughly by peeking (optional, or start from 0 and limit count?)
        # Better to start from "now" to avoid flooding history on startup, 
        # OR fetch last 1 minute.
        # We will assume startup = fetch last 100 events or just start new.
        # User said "Bound throughput: do not emit massive history; emit only new events."
        self.initialized = False

    async def init_cursors(self):
        """Finds the latest RecordId for each log to start streaming from there."""
        logger.info("Initializing Event Stream Cursors...")
        for log_name in self.cursors.keys():
            # Get the single most recent record ID
            cmd = f"Get-WinEvent -LogName {log_name} -MaxEvents 1 | Select-Object -ExpandProperty RecordId"
            res = run_powershell(cmd, json_output=False)
            if res and res.strip().isdigit():
                self.cursors[log_name] = int(res.strip())
        self.initialized = True
        logger.info(f"Cursors initialized: {self.cursors}")

    async def event_generator(self):
        try:
            if not self.initialized:
                await self.init_cursors()

            while True:
                has_updates = False
                for log_name, event_ids in GROUPED_FILTERS.items():
                    # Check connection state logic or breaks here if needed
                    
                    last_id = self.cursors.get(log_name, 0)
                    
                    id_query = " or ".join([f"EventID={eid}" for eid in event_ids])
                    xml_query = f"""
                    <QueryList>
                      <Query Id="0" Path="{log_name}">
                        <Select Path="{log_name}">*[System[({id_query}) and EventRecordID &gt; {last_id}]]</Select>
                      </Query>
                    </QueryList>
                    """
                    # Minified for PS string safety
                    xml_query = xml_query.replace("\n", "").replace("  ", "")
                    
                    ps_script = f"""
                    try {{
                        $events = Get-WinEvent -FilterXml '{xml_query}' -ErrorAction Stop
                        $events | Sort-Object RecordId | ForEach-Object {{
                            [pscustomobject]@{{
                                type = 'event'
                                recordId = $_.RecordId
                                timeCreated = $_.TimeCreated.ToString('o')
                                logName = $_.LogName
                                provider = $_.ProviderName
                                eventId = $_.Id
                                level = $_.LevelDisplayName
                                message = if ($_.Message) {{ $_.Message.Substring(0, [Math]::Min($_.Message.Length, 4000)) }} else {{ "" }}
                                raw = if ($_.LogName -eq 'System' -and $_.ProviderName -eq 'Microsoft-Windows-WHEA-Logger') {{ $_.ToXml() }} else {{ $null }}
                            }}
                        }} | ConvertTo-Json -Depth 5
                    }} catch {{
                         # If it fails (e.g. no events found), return empty JSON list and clean exit
                         Write-Output "[]"
                         exit 0
                    }}
                    """

                    data = run_powershell(ps_script)
                    
                    if data:
                        if isinstance(data, dict):
                            data = [data]
                        
                        for event in data:
                            rid = event.get('recordId')
                            if rid and rid > self.cursors[log_name]:
                                self.cursors[log_name] = rid
                            
                            yield f"event: message\ndata: {json.dumps(event)}\n\n"
                            has_updates = True

                # Always yield heartbeat to keep connection alive
                yield f"event: heartbeat\ndata: {json.dumps({ 'type': 'heartbeat', 'ts': datetime.now().isoformat() })}\n\n"

                await asyncio.sleep(5)

        except Exception as e:
            logger.exception("Event Stream Generator Crashed")
            # Yield an error event so frontend knows? 
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

