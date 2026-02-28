import logging
from .powershell import run_powershell

logger = logging.getLogger(__name__)

def get_recent_critical_events(max_events=20):
    """
    Fetches the most recent Critical (1) and Error (2) level events from the System log.
    Includes event properties for the Inspector.
    """
    script = f"""
    Get-WinEvent -FilterHashtable @{{LogName='System'; Level=1,2}} -MaxEvents {max_events} -ErrorAction SilentlyContinue | 
    Select-Object @{{Name='TimeCreated'; Expression={{$_.TimeCreated.ToString('o')}}}}, Id, LevelDisplayName, ProviderName, Message, @{{Name='Properties'; Expression={{$_.Properties | ForEach-Object {{ $_.Value }} }} }} | 
    ConvertTo-Json -Depth 2
    """
    try:
        results = run_powershell(script)
        if results is None: return []
        if isinstance(results, dict): return [results]
        return results
    except Exception as e:
        logger.error(f"Error fetching critical events: {e}")
        return []

def get_whea_events(max_events=30):
    """
    Fetches the most recent events from the WHEA-Logger provider.
    Extracts the binary 'RawData' property for decoded hardware error analysis.
    """
    script = f"""
    Get-WinEvent -FilterHashtable @{{ProviderName='Microsoft-Windows-WHEA-Logger'}} -MaxEvents {max_events} -ErrorAction SilentlyContinue | 
    Select-Object @{{Name='TimeCreated'; Expression={{$_.TimeCreated.ToString('o')}}}}, Id, LevelDisplayName, Message, @{{Name='RawData'; Expression={{ $raw = $_.Properties | Where-Object {{ $_.Value -is [byte[]] }} | Select-Object -First 1; if ($raw) {{ [System.BitConverter]::ToString($raw.Value).Replace('-','') }} else {{ $null }} }} }} | 
    ConvertTo-Json
    """
    try:
        results = run_powershell(script)
        if results is None: return []
        if isinstance(results, dict): return [results]
        return results
    except Exception as e:
        logger.error(f"Error fetching WHEA events: {e}")
        return []

def get_recent_system_events(max_events=50):
    """
    Fetches general system events for trend analysis.
    """
    script = f"""
    Get-WinEvent -LogName 'System' -MaxEvents {max_events} -ErrorAction SilentlyContinue | 
    Select-Object @{{Name='TimeCreated'; Expression={{$_.TimeCreated.ToString('o')}}}}, Id, LevelDisplayName, ProviderName, Message | 
    ConvertTo-Json
    """
    try:
        results = run_powershell(script)
        if results is None: return []
        if isinstance(results, dict): return [results]
        return results
    except Exception as e:
        logger.error(f"Error fetching system events: {e}")
        return []

def get_events_prior_to_timestamp(target_time_iso, max_events=50):
    """
    Fetches the last N events from the System log specifically BEFORE a target timestamp.
    Used for "Pre-Crash Context" to find the last signals before a silent freeze.
    """
    script = f"""
    $TargetTime = [DateTime]'{target_time_iso}'
    $FilterXml = @"
<QueryList>
  <Query Id="0" Path="System">
    <Select Path="System">*[System[TimeCreated[@SystemTime&lt;'$($TargetTime.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.000Z"))']]]</Select>
  </Query>
</QueryList>
"@
    Get-WinEvent -FilterXml $FilterXml -MaxEvents {max_events} -ErrorAction SilentlyContinue | 
    Select-Object @{{Name='TimeCreated'; Expression={{$_.TimeCreated.ToString('o')}}}}, Id, LevelDisplayName, ProviderName, Message | 
    ConvertTo-Json
    """
    
    try:
        results = run_powershell(script)
        if results is None: return []
        if isinstance(results, dict): return [results]
        return results
    except Exception as e:
        logger.error(f"Error fetching prior context events: {e}")
        return []
