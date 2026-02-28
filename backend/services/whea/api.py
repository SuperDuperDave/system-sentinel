from fastapi import APIRouter, HTTPException, BackgroundTasks
from typing import List, Optional
from datetime import datetime, timezone

from .models import (
    WheaSettingsResponse, WheaBucketsResponse, WheaSignatureResponse,
    WheaStormResponse, StormWindow
)
from .store import WheaStore
from .storms import WheaStormDetector
from .ingest import WheaIngestor
from .parse import WheaParser
from .aggregate import WheaSignatureGenerator
from .render import WheaContextRenderer

router = APIRouter(prefix="/api/whea", tags=["whea"])

# --- Dependency Injection (Singleton) ---
def get_store():
    return WheaStore.get_instance()

# --- Background Ingestion Task ---
def run_ingest_cycle():
    """
    Orchestrates the ingestion pipeline.
    Ingest -> Parse -> Aggregate -> Store
    """
    store = get_store()
    current_watermark = store.get_watermark()
    
    # 1. Ingest (Pure)
    raw_events = WheaIngestor.fetch_events(since_record_id=current_watermark)
    
    if not raw_events:
        return
        
    for raw in raw_events:
        # 2. Parse (Pure)
        event = WheaParser.parse_event(raw)
        
        # 3. Aggregate (Pure)
        sig_id, sig_key, desc = WheaSignatureGenerator.compute_signature(event)
        event.signature_id = sig_id
        
        # 4. Store (Transactional)
        store.apply_event(event, sig_key, sig_id, desc)

# --- Endpoints ---

@router.get("/settings", response_model=WheaSettingsResponse)
async def get_settings():
    s = get_store().settings
    return WheaSettingsResponse(
        history_days=s.get("history_days", 30),
        bucket_seconds=s.get("bucket_seconds", 60),
        burst_threshold=s.get("burst_threshold", 5),
        accel_threshold=s.get("accel_threshold", 2.0)
    )

@router.patch("/settings")
async def update_settings(settings: WheaSettingsResponse):
    store = get_store()
    new_conf = {
        "history_days": settings.history_days,
        "bucket_seconds": settings.bucket_seconds,
        "burst_threshold": settings.burst_threshold,
        "accel_threshold": settings.accel_threshold
    }
    store.update_settings(new_conf)
    return {"status": "updated", "config": new_conf}

@router.post("/ingest")
async def trigger_ingest(background_tasks: BackgroundTasks):
    """
    Manually trigger an ingestion cycle (e.g. on page refresh).
    In prod, this might also run on a schedule.
    """
    background_tasks.add_task(run_ingest_cycle)
    return {"status": "ingestion_started"}

@router.get("/buckets", response_model=WheaBucketsResponse)
async def get_buckets(start: Optional[int] = None, end: Optional[int] = None):
    """
    Returns time-series buckets for charting.
    Targeting UTC epochs.
    """
    store = get_store()
    
    # Default: Last 24h
    if not end: end = int(datetime.now(timezone.utc).timestamp())
    if not start: start = end - (24 * 3600)
    
    buckets = store.get_buckets(start, end)
    
    # Transform for API
    data = []
    for b in buckets:
        item = {
            "timestamp": b.bucket_start_epoch, 
            "total": b.count_total 
            # Could expand signatures here if requested
        }
        data.append(item)
        
    return WheaBucketsResponse(
        start_epoch=start,
        end_epoch=end,
        granularity=store.settings.get("bucket_seconds", 60),
        data=data
    )

@router.get("/signatures", response_model=List[WheaSignatureResponse])
async def get_signatures(sort: str = "impact", limit: int = 50):
    store = get_store()
    sigs = list(store.signatures.values())
    
    # Sorting
    if sort == "impact": # Count 24h
        sigs.sort(key=lambda x: x.count_24h, reverse=True)
    elif sort == "total":
        sigs.sort(key=lambda x: x.count_total, reverse=True)
    elif sort == "recent":
        sigs.sort(key=lambda x: x.last_seen, reverse=True)
        
    sigs = sigs[:limit]
    
    # Transform
    res = []
    for s in sigs:
        res.append(WheaSignatureResponse(
            id=s.signature_id,
            description=s.description,
            error_type=s.error_type,
            count_24h=s.count_24h,
            count_total=s.count_total,
            last_seen=s.last_seen.isoformat(),
            acceleration=1.0, # Todo: Compute on fly if needed
            samples=s.samples
        ))
    return res

@router.get("/storms", response_model=WheaStormResponse)
async def get_current_storm():
    """
    Returns detection status.
    """
    store = get_store()
    detector = WheaStormDetector(store)
    storm = detector.detect()
    
    return WheaStormResponse(
        id=storm.storm_id,
        start=storm.start_time.isoformat(),
        end=storm.end_time.isoformat() if storm.end_time else None,
        status=storm.status,
        severity=storm.severity,
        reason=storm.reason,
        peak_rate=storm.peak_rate,
        dominant_signatures=storm.dominant_signature_ids
    )

@router.post("/context/generate")
async def generate_context(full_appendix: bool = False):
    """
    Generates the AI context block.
    """
    store = get_store()
    renderer = WheaContextRenderer(store)
    text = renderer.render(full_appendix=full_appendix)
    return {"context": text}
