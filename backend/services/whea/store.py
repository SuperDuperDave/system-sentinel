from threading import Lock
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone
import math

from .models import WheaEvent, WheaSignatureStats, TimeBucket

class WheaStore:
    """
    Thread-safe in-memory store for WHEA telemetry.
    Manages two retention layers:
    1. TimeBuckets (Layer A): High-speed time-series for charts & detection.
    2. Signatures (Layer B): Aggregated stats per error pattern.
    """
    _instance = None
    _lock = Lock()

    def __init__(self):
        self.buckets: Dict[int, TimeBucket] = {}  # timestamp_epoch -> TimeBucket
        self.signatures: Dict[str, WheaSignatureStats] = {} # signature_id -> Stats
        self.settings: Dict[str, int] = {
            "history_days": 30,
            "bucket_seconds": 60
        }
        self.last_ingested_record_id: int = 0
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def update_settings(self, new_settings: Dict[str, int]):
        """
        Updates settings and triggers immediate pruning if history window shrunk.
        """
        with self._lock:
            self.settings.update(new_settings)
            self._prune_unsafe()

    def apply_event(self, event: WheaEvent, signature_key: str, signature_id: str, description: str):
        """
        Transactional-ish apply of a normalized event.
        Updates both bucket counts and signature stats.
        """
        with self._lock:
            # 1. Update Watermark
            self.last_ingested_record_id = max(self.last_ingested_record_id, event.record_id)
            
            # 2. Get/Create Signature Stats
            if signature_id not in self.signatures:
                self.signatures[signature_id] = WheaSignatureStats(
                    signature_id=signature_id,
                    signature_key=signature_key,
                    first_seen=event.time_created,
                    last_seen=event.time_created,
                    error_type=event.error_type,
                    description=description
                )
            
            sig = self.signatures[signature_id]
            sig.last_seen = max(sig.last_seen, event.time_created)
            sig.count_total += 1
            
            # Rolling 24h count (approximate or recomputed)
            # For strict correctness, we'd sum buckets. For now, simple increment.
            # A background job could recompute this from buckets if strictness needed.
            sig.count_24h += 1 

            # Update Sample (Max 3)
            if len(sig.samples) < 3:
                 # Minimal sample payload
                 sample = {
                     "time": event.time_created.isoformat(),
                     "msg": event.message[:200], # truncated
                     "rec_id": event.record_id
                 }
                 if event.raw_xml:
                     sample["xml_snippet"] = event.raw_xml[:500] 
                 sig.samples.append(sample)

            # 3. Update Time Bucket
            bucket_sec = self.settings["bucket_seconds"]
            epoch = int(event.time_created.timestamp())
            bucket_start = math.floor(epoch / bucket_sec) * bucket_sec
            
            if bucket_start not in self.buckets:
                self.buckets[bucket_start] = TimeBucket(bucket_start_epoch=bucket_start)
            
            bucket = self.buckets[bucket_start]
            bucket.count_total += 1
            bucket.signatures[signature_id] = bucket.signatures.get(signature_id, 0) + 1

    def prune(self):
        """
        Public prune method.
        """
        with self._lock:
            self._prune_unsafe()

    def _prune_unsafe(self):
        """
        Internal prune logic. Assumes lock held.
        """
        days = self.settings.get("history_days", 30)
        cutoff_seconds = datetime.now(timezone.utc).timestamp() - (days * 86400)
        
        # Prune Buckets
        keys_to_remove = [k for k in self.buckets if k < cutoff_seconds]
        for k in keys_to_remove:
            del self.buckets[k]
            
        # Optional: Prune Signatures that haven't been seen in retention window?
        # User requested retention applies to history. 
        # For signatures, we might keep them longer or prune if last_seen < cutoff.
        # Let's prune simple for now.
        sigs_to_remove = [k for k, v in self.signatures.items() if v.last_seen.timestamp() < cutoff_seconds]
        for k in sigs_to_remove:
            del self.signatures[k]

    def get_buckets(self, start_epoch: int, end_epoch: int) -> List[TimeBucket]:
        with self._lock:
             # Filter and sort
             return sorted(
                 [b for t, b in self.buckets.items() if start_epoch <= t <= end_epoch],
                 key=lambda x: x.bucket_start_epoch
             )

    def get_watermark(self) -> int:
        with self._lock:
            return self.last_ingested_record_id
