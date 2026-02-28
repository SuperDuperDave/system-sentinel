from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Literal
from datetime import datetime

# --- Internal Domain Models ---

class WheaEvent(BaseModel):
    """
    Normalized internal representation of a WHEA event.
    """
    record_id: int
    time_created: datetime
    event_id: int
    provider_name: str
    message: str
    raw_xml: Optional[str] = None # Retain only if needed, or stripped later
    
    # Extracted Fields
    error_type: str = "Unknown"
    corrected: bool = False
    
    # Hardware Specifics (Optional)
    bank: Optional[str] = None
    apic_id: Optional[str] = None
    mci_status: Optional[str] = None
    vendor_id: Optional[str] = None
    device_id: Optional[str] = None
    
    # Signature
    signature_id: Optional[str] = None

class WheaSignatureStats(BaseModel):
    """
    Aggregated statistics for a unique error signature.
    """
    signature_id: str
    signature_key: str # Human readable key
    first_seen: datetime
    last_seen: datetime
    count_total: int = 0
    count_24h: int = 0
    
    # Features for UI/AI
    error_type: str
    description: str # Short description for UI
    
    # Sample retention (Bounded)
    samples: List[Dict[str, Any]] = Field(default_factory=list) # max 3

class BucketPoint(BaseModel):
    """
    Time-series point for a specific signature in a bucket.
    """
    signature_id: str
    count: int

class TimeBucket(BaseModel):
    """
    A specific time slice (e.g., 1 minute) aggregating events.
    """
    bucket_start_epoch: int # UTC Timestamp of bucket start
    count_total: int = 0
    signatures: Dict[str, int] = Field(default_factory=dict) # signature_id -> count

class StormWindow(BaseModel):
    """
    Detected storm interval.
    """
    storm_id: str
    start_time: datetime
    end_time: Optional[datetime] = None
    status: Literal["active", "resolved"] = "active"
    severity: Literal["warning", "critical"] = "warning"
    peak_rate: float = 0.0 # events per bucket
    dominant_signature_ids: List[str] = []
    reason: str # Explainable reason (e.g. "Burst > 50/min")


# --- API Response Models ---

class WheaSettingsResponse(BaseModel):
    history_days: int
    bucket_seconds: int
    burst_threshold: int
    accel_threshold: float

class WheaStormResponse(BaseModel):
    id: str
    start: str
    end: Optional[str]
    status: str
    severity: str
    reason: str
    peak_rate: float
    dominant_signatures: List[str] # Detailed objects or just IDs? IDs for now.

class WheaSignatureResponse(BaseModel):
    id: str
    description: str
    error_type: str
    count_24h: int
    count_total: int
    last_seen: str
    acceleration: float # Computed on fly or stored
    samples: List[Dict[str, Any]] = []    

class WheaBucketsResponse(BaseModel):
    start_epoch: int
    end_epoch: int
    granularity: int
    data: List[Dict[str, Any]] # format: {timestamp: 123, total: 5, sig_abc: 2, ...}
