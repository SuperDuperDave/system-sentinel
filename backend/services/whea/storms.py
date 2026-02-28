from typing import List, Optional
from datetime import datetime, timezone
import statistics

from .models import StormWindow, TimeBucket
from .store import WheaStore

class WheaStormDetector:
    """
    Analyzes TimeBuckets to detect storm conditions.
    Logic is purely functional based on store state.
    """
    
    def __init__(self, store: WheaStore):
        self.store = store

    def detect(self) -> StormWindow:
        """
        Runs detection over recent buckets.
        Returns a StormWindow representing the CURRENT status.
        """
        settings = self.store.settings
        bucket_sec = settings.get("bucket_seconds", 60)
        burst_threshold = settings.get("burst_threshold", 5) # e.g. 5 events/min
        accel_threshold = settings.get("accel_threshold", 2.0) # 2x baseline
        
        # Windows
        now = datetime.now(timezone.utc)
        now_epoch = int(now.timestamp())
        
        # Recent Window (e.g. last 10 mins)
        recent_window_sec = settings.get("whea_recent_window_buckets", 10) * bucket_sec
        recent_buckets = self.store.get_buckets(now_epoch - recent_window_sec, now_epoch)
        
        if not recent_buckets:
            return self._clean_state(now)

        # 1. Burst Check
        # Check if any single bucket in recent window exceeds burst threshold
        peak_bucket = max(recent_buckets, key=lambda b: b.count_total)
        peak_rate = peak_bucket.count_total
        
        if peak_rate >= burst_threshold:
             dom_sigs = self._get_dominant_signatures(recent_buckets)
             return StormWindow(
                 storm_id=f"storm_{now_epoch}",
                 start_time=now, # Ideally this would track actual start of burst
                 status="active",
                 severity="critical" if peak_rate > (burst_threshold * 2) else "warning",
                 peak_rate=float(peak_rate),
                 dominant_signature_ids=dom_sigs,
                 reason=f"Burst detected: {peak_rate} events/min (Threshold: {burst_threshold})"
             )

        # 2. Acceleration Check
        # Compare Recent Avg Rate vs Baseline Avg Rate
        # Baseline = (Now - 4h) to (Now - 10m)
        baseline_window_sec = settings.get("whea_baseline_window_buckets", 240) * bucket_sec
        baseline_end = now_epoch - recent_window_sec
        baseline_start = baseline_end - baseline_window_sec
        
        baseline_buckets = self.store.get_buckets(baseline_start, baseline_end)
        
        recent_avg = statistics.mean([b.count_total for b in recent_buckets]) if recent_buckets else 0
        baseline_avg = statistics.mean([b.count_total for b in baseline_buckets]) if baseline_buckets else 0
        
        # Prevent division by zero / noise amp
        if baseline_avg < 0.1: baseline_avg = 0.1 
        
        acceleration = recent_avg / baseline_avg
        
        if acceleration >= accel_threshold and recent_avg > 0.5: # Min floor to avoid 0.1 -> 0.3 triggering
             return StormWindow(
                 storm_id=f"accel_{now_epoch}",
                 start_time=now,
                 status="active",
                 severity="warning",
                 peak_rate=float(peak_rate),
                 dominant_signature_ids=self._get_dominant_signatures(recent_buckets),
                 reason=f"Accelerating Error Rate: {acceleration:.1f}x baseline ({recent_avg:.2f}/min vs {baseline_avg:.2f}/min)"
             )

        # No storm
        return self._clean_state(now)

    def _clean_state(self, now: datetime) -> StormWindow:
        return StormWindow(
            storm_id="none",
            start_time=now,
            status="resolved",
            severity="warning", # Default
            reason="No active storms detected."
        )

    def _get_dominant_signatures(self, buckets: List[TimeBucket]) -> List[str]:
        # Aggregate counts across provided buckets
        totals = {}
        for b in buckets:
            for sig, count in b.signatures.items():
                totals[sig] = totals.get(sig, 0) + count
        
        # Sort desc
        sorted_sigs = sorted(totals.items(), key=lambda x: x[1], reverse=True)
        return [s[0] for s in sorted_sigs[:3]] # Top 3
