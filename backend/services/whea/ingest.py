from datetime import datetime, timezone
from typing import List, Dict, Any
import subprocess
import json
import logging

# Circular imports? We need models but not other services yet.
# We will just yield raw dicts, letting a controller orchestrate.
# Or if we want strict types, meaningful error handling.

logger = logging.getLogger(__name__)

from services.powershell import run_powershell

class WheaIngestor:
    """
    Pure efficient ingestion from Windows Event Log.
    Does NOT mutate store directly. Returns raw records.
    """
    
    @staticmethod
    def fetch_events(since_record_id: int = 0, max_events: int = 1000) -> List[Dict[str, Any]]:
        """
        Fetches WHEA-Logger events > since_record_id.
        Returns list of raw dicts containing: Id (EventId), RecordId, Message, TimeCreated, etc.
        """
        
        # PowerShell Script
        # We MUST select RecordId to handle watermarking.
        # EventId is separate.
        
        ps_script = f"""
        $Query = @"
        <QueryList>
          <Query Id="0" Path="System">
            <Select Path="System">
              *[System[Provider[@Name='Microsoft-Windows-WHEA-Logger'] and EventRecordID > {since_record_id}]]
            </Select>
          </Query>
        </QueryList>
"@
        Get-WinEvent -FilterXml $Query -MaxEvents {max_events} -ErrorAction SilentlyContinue |
        Select-Object Id, RecordId, LevelDisplayName, Message, ProviderName, @{{Name='TimeCreated'; Expression={{$_.TimeCreated.ToUniversalTime().ToString('o')}}}} |
        ConvertTo-Json -Depth 2
        """
        
        try:
            # Use shared helper which handles powershell.exe, encoding, and JSON parsing
            data = run_powershell(ps_script, json_output=True)
            
            if not data:
                return []
                
            # Normalize to list
            if isinstance(data, dict):
                return [data]
            return data
            
        except Exception as e:
            logger.error(f"Ingest unexpected error: {e}")
            return []
