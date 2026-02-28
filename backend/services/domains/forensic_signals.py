import asyncio
import logging
import datetime
from typing import Dict, Any, List

from ..system_logs import get_recent_system_events
from .hardware import get_hardware_overview
from .pcie import get_pcie_fabric_context
from .power import get_power_context
from .constraints import get_constraints_context

logger = logging.getLogger(__name__)

async def get_forensic_signals_context() -> Dict[str, Any]:
    """
    The Signal Engine: Aggregates data from diagnostic domains and derives high-level forensic signals.
    Classes: Suppressions, Gaps, Pressure, Transitions, Mismatches.
    """
    try:
        # 1. Fetch Source Data
        tasks = {
            "hardware": get_hardware_overview(),
            "pcie": get_pcie_fabric_context(),
            "power": get_power_context(),
            "constraints": get_constraints_context(),
            "logs": asyncio.to_thread(get_recent_system_events, 200)
        }
        
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        res_map = dict(zip(tasks.keys(), results))
        
        def _safe_res(key, default=None):
            val = res_map.get(key)
            if isinstance(val, Exception) or val is None:
                return default
            return val

        # 2. Initialize Buckets
        buckets = {
            "suppressions": [],
            "gaps": [],
            "pressure": [],
            "transitions": [],
            "mismatches": []
        }

        # 3. Parse Event Logs for Timeline and Markers
        logs = _safe_res("logs", [])
        timeline_events = []
        markers = {
            "had_sleep": False,
            "had_wake": False,
            "had_gpu_reset": False,
            "had_storage_timeout": False
        }
        
        # Detect markers and build timeline
        for evt in logs[:100]:  # Last 100 events for timeline
            provider = evt.get("ProviderName", "")
            evt_id = evt.get("Id", 0)
            msg_raw = evt.get("Message")
            msg = (msg_raw or "").lower()
            
            # Sleep/Wake Detection
            if evt_id == 42 or "entering sleep" in msg:
                markers["had_sleep"] = True
            if evt_id == 1 or "resuming from sleep" in msg or "wake" in msg:
                markers["had_wake"] = True
                
            # GPU Reset Detection  
            if "display" in provider.lower() or "nvlddmkm" in provider.lower():
                if evt_id == 4101 or "reset" in msg or "tdr" in msg:
                    markers["had_gpu_reset"] = True
                    
            # Storage Timeout Detection
            if "stor" in provider.lower() or "disk" in provider.lower():
                if "timeout" in msg or evt_id == 129:
                    markers["had_storage_timeout"] = True
            
            # Add to timeline
            timeline_events.append({
                "TimeCreated": evt.get("TimeCreated"),
                "Id": evt_id,
                "ProviderName": provider,
                "LevelDisplayName": evt.get("LevelDisplayName", "Information"),
                "Message": evt.get("Message", "")[:200]  # Truncate for UI
            })

        # --- 4. SUPPRESSIONS ---
        hw_config = _safe_res("hardware", {}).get("config", {})
        constraints = _safe_res("constraints", {})
        
        # Fast Startup
        if hw_config.get("fast_startup"):
            buckets["suppressions"].append({
                "id": "suppression:fast-startup",
                "class": "suppressions",
                "severity": "low",
                "title": "Fast Startup Active",
                "summary": "Full hardware re-initialization is bypassed on Shutdown, potentially hiding transient issues.",
                "evidence": {"HiberbootEnabled": 1}
            })
        
        # Disabled Devices (Code 22)
        for item in constraints.get("active_constraints", []):
            if item.get("type") == "device_disabled":
                buckets["suppressions"].append({
                    "id": f"suppression:device:{item.get('instance_id', 'unk')}",
                    "class": "suppressions",
                    "severity": "medium",
                    "title": f"Device Disabled: {item.get('friendly_name', 'Unknown')}",
                    "summary": "Device manually disabled via Device Manager (Code 22).",
                    "evidence": item
                })

        # --- 5. GAPS ---
        hw_details = _safe_res("hardware", {}).get("details", {})
        
        # Missing Drivers (Code 28)
        for component, data in hw_details.items():
            if isinstance(data, dict):
                prob_code = data.get("config", {}).get("problem_code")
                if prob_code == 28:
                    buckets["gaps"].append({
                        "id": f"gap:driver:{component}",
                        "class": "gaps",
                        "severity": "high",
                        "title": f"Missing Driver: {component.upper()}",
                        "summary": f"Device reported Code 28. Operational telemetry is missing.",
                        "evidence": data
                    })
                # Other error states
                elif prob_code and prob_code not in [0, 22]:
                    buckets["gaps"].append({
                        "id": f"gap:error:{component}",
                        "class": "gaps",
                        "severity": "medium",
                        "title": f"Device Error: {component.upper()}",
                        "summary": f"ConfigManagerErrorCode {prob_code}",
                        "evidence": data
                    })

        # --- 6. PRESSURE ---
        # Event Velocity Analysis
        if len(logs) > 0:
            # Count events by provider
            provider_counts = {}
            for evt in logs:
                prov = evt.get("ProviderName", "Unknown")
                provider_counts[prov] = provider_counts.get(prov, 0) + 1
            
            # Find top talkers (>10 events)
            for prov, count in sorted(provider_counts.items(), key=lambda x: x[1], reverse=True)[:3]:
                if count > 10:
                    buckets["pressure"].append({
                        "id": f"pressure:talker:{prov}",
                        "class": "pressure",
                        "severity": "high" if count > 50 else "medium",
                        "title": f"High Event Rate: {prov}",
                        "summary": f"{count} events detected in recent window (~{count//2} events/min est.)",
                        "evidence": {"provider": prov, "count": count}
                    })
        
        # Ambient baseline
        if len(buckets["pressure"]) == 0:
            buckets["pressure"].append({
                "id": "pressure:ambient",
                "class": "pressure",
                "severity": "low",
                "title": "Ambient Signal Velocity",
                "summary": "Baseline noise floor is nominal.",
                "evidence": {"event_count": len(logs)}
            })

        # --- 7. TRANSITIONS ---
        power_data = _safe_res("power", {})
        uptime_sec = power_data.get("uptime_seconds", 0)
        fast_startup = power_data.get("fast_startup", False)
        
        # Long uptime + Fast Startup = hidden state
        if fast_startup and uptime_sec > 604800:  # 7 days
            buckets["transitions"].append({
                "id": "transition:fast-startup-uptime",
                "class": "transitions",
                "severity": "medium",
                "title": "Long Uptime + Fast Startup",
                "summary": f"System has not cold-booted in {uptime_sec // 86400} days. Transient issues may be masked.",
                "evidence": {"uptime_days": uptime_sec // 86400, "fast_startup": True}
            })
        
        # GPU Reset near Wake
        if markers["had_wake"] and markers["had_gpu_reset"]:
            buckets["transitions"].append({
                "id": "transition:gpu-reset-wake",
                "class": "transitions",
                "severity": "high",
                "title": "GPU Reset Proximity to Wake",
                "summary": "GPU TDR/reset detected in same window as system wake event.",
                "evidence": markers
            })

        # --- 8. MISMATCHES ---
        # Driver Age Check
        for component, data in hw_details.items():
            if isinstance(data, dict):
                driver_date = data.get("identity", {}).get("date")
                if driver_date:
                    try:
                        d_date = datetime.datetime.strptime(driver_date, "%Y-%m-%d")
                        age_days = (datetime.datetime.now() - d_date).days
                        if age_days > 730:  # 2 years
                            buckets["mismatches"].append({
                                "id": f"mismatch:age:{component}",
                                "class": "mismatches",
                                "severity": "medium",
                                "title": f"Legacy Driver: {component.upper()}",
                                "summary": f"Driver is {age_days} days old ({driver_date}). High risk of incompatibility.",
                                "evidence": {"date": driver_date, "age_days": age_days}
                            })
                    except: pass

        # --- 9. Signal Ranking ---
        ranked = []
        for b_list in buckets.values():
            ranked.extend(b_list)
        
        sev_prio = {"high": 3, "medium": 2, "low": 1}
        ranked.sort(key=lambda x: sev_prio.get(x.get("severity", "low"), 0), reverse=True)

        return {
            "buckets": buckets,
            "ranked": ranked,
            "timeline": {
                "events": timeline_events,
                "window_minutes": 60,  # Approximate based on recent logs
                "markers": markers
            },
            "timestamp": datetime.datetime.now().isoformat()
        }

    except Exception as e:
        logger.exception(f"Signal Engine error: {e}")
        return {
            "buckets": buckets if 'buckets' in locals() else {},
            "ranked": [],
            "timeline": {"events": [], "window_minutes": 0, "markers": {}},
            "error": str(e),
            "timestamp": datetime.datetime.now().isoformat()
        }

