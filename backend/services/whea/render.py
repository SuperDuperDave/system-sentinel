from typing import List, Optional
from .store import WheaStore
from .storms import WheaStormDetector
from .models import StormWindow

class WheaContextRenderer:
    """
    Generates AI-ready context blocks from WHEA Store.
    Strategy: Summary-first. Avoid spam.
    """
    
    def __init__(self, store: WheaStore):
        self.store = store
        self.detector = WheaStormDetector(store)

    def render(self, include_samples: bool = False, full_appendix: bool = False) -> str:
        """
        Produces the context string.
        """
        # 1. Detect Current Storm Status
        storm: StormWindow = self.detector.detect()
        
        # 2. Get Top Signatures (Ranked by Impact/Recent)
        # Simplified rank: Sort by count_24h desc
        all_sigs = list(self.store.signatures.values())
        top_sigs = sorted(all_sigs, key=lambda s: s.count_24h, reverse=True)[:5]
        
        # --- HEADER ---
        lines = []
        lines.append(f"### CORRECTED WHEA STORM RADAR")
        
        status_icon = "🔴" if storm.severity == "critical" else "⚠️" if storm.status == "active" else "🟢"
        lines.append(f"**Status**: {status_icon} {storm.status.upper()} ({storm.reason})")
        
        if storm.status == "active":
             lines.append(f"- **Peak Rate**: {storm.peak_rate:.1f} events/min")
             lines.append(f"- **Dominant Signatures**: {', '.join(storm.dominant_signature_ids)}")
        
        lines.append("")
        
        # --- TOP SIGNATURES ---
        if not top_sigs:
            lines.append("_No corrected hardware errors recorded in history window._")
            return "\n".join(lines)

        lines.append(f"**Top {len(top_sigs)} Failure Signatures (Last 24h):**")
        
        for idx, sig in enumerate(top_sigs):
            lines.append(f"{idx+1}. **{sig.description}**")
            lines.append(f"   - Count (24h): {sig.count_24h} | Total: {sig.count_total}")
            lines.append(f"   - Last Seen: {sig.last_seen.isoformat()}")
            lines.append(f"   - Signature ID: `{sig.signature_id[:8]}...`")
            
            # Samples (If requested or if storm is active?)
            # Strategy: Always show 1 short sample line if available to ground the AI?
            # User guideline: "1 sample message per signature (optional)"
            if sig.samples:
                # Show more context for AI analysis, truncate only if excessive
                msg = latest['msg']
                if len(msg) > 500:
                    msg = msg[:500] + "..."
                lines.append(f"   - *Sample*: \"{msg}\"")
        
        lines.append("")

        # --- APPENDIX (Full Logs) ---
        if full_appendix:
            lines.append("#### APPENDIX: RAW SCAMPLES")
            for sig in top_sigs:
                lines.append(f"--- Signature: {sig.signature_id} ---")
                for s in sig.samples:
                    lines.append(f"Time: {s['time']}")
                    lines.append(f"Msg: {s['msg']}")
                    if 'xml_snippet' in s:
                        lines.append(f"XML: {s['xml_snippet']}")
                    lines.append("")

        return "\n".join(lines)
