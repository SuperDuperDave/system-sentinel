import logging
import json
from typing import Dict, Any, List
from services.powershell import run_powershell

logger = logging.getLogger(__name__)

async def get_constraints_context() -> Dict[str, Any]:
    """
    Retrieves Active Constraints (User-Disabled Devices).
    - Physics: Devices with ProblemCode 22 (CM_PROB_HARDWARE_DISABLED)
    - Status: Devices in Error state.
    """
    try:
        script = _get_constraints_script()
        data = run_powershell(script)
        
        if data and isinstance(data, dict):
            return data
            
        return {"error": "Invalid data returned from Constraints service"}
        
    except Exception as e:
        logger.error(f"Constraints context fetch failed: {e}")
        return {"error": str(e)}

def _get_constraints_script() -> str:
    return r"""
    $ErrorActionPreference = "SilentlyContinue"
    
    # scan for disabled or error devices
    # Problem Code 22 is explicitly "Disabled by User"
    
    $disabled = Get-PnpDevice -Status Error -PresentOnly 
    if (-not $disabled) { $disabled = @() }
    
    $active_constraints = @()
    
    foreach ($d in $disabled) {
        # We try to distinguish "User Disabled" from "Crashed"
        # ProblemDescription usually says "This device is disabled. (Code 22)"
        
        $intent = "unknown"
        if ($d.Problem -eq 22 -or $d.ProblemDescription -like "*disabled*") {
            $intent = "user_disabled"
        } else {
            $intent = "fault"
        }
        
        $active_constraints += @{
            instance_id = $d.InstanceId
            friendly_name = $d.FriendlyName
            class = $d.Class
            problem_code = $d.Problem
            problem_description = $d.ProblemDescription
            detected_intent = $intent
        }
    }
    
    @{
        active_constraints = $active_constraints
    } | ConvertTo-Json -Depth 3
    """
