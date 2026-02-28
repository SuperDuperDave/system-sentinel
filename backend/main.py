# Force Reload 1
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import os

app = FastAPI(
    title="System Sentinel MCP",
    description="Backend for System Sentinel - Diagnostics & Log Analysis",
    version="0.2.0"
)

# Collectors
from collectors.events import EventCollector, WheaCollector
from collectors.dumps import DumpCollector
from collectors.system import SystemCollector
from services.capture import CapturePackService
import os
from fastapi.responses import FileResponse
from fastapi import BackgroundTasks

# WHEA Router
from services.whea.api import router as whea_router
app.include_router(whea_router)

# Allow CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify the frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"status": "online", "system": "System Sentinel", "version": "0.1.0"}

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

@app.get("/api/collectors/events")
async def collect_events():
    return EventCollector().collect()

@app.get("/api/collectors/whea")
async def collect_whea():
    return WheaCollector().collect()

@app.get("/api/collectors/dumps")
async def collect_dumps():
    return DumpCollector().collect()

@app.get("/api/collectors/system")
async def collect_system():
    return SystemCollector().collect()

@app.post("/api/capture/create")
async def create_capture_pack(background_tasks: BackgroundTasks):
    # Save to user's Documents folder if possible, otherwise local 'captures' folder
    # In WSL, /mnt/c/Users/<user>/Documents is hard to guess generically without input
    # For MVP, let's use a local 'captures' folder in the backend dir
    output_dir = os.path.join(os.getcwd(), "captures")
    service = CapturePackService(output_dir)
    result = await service.create_pack()
    
    if result.get("success"):
        # Schedule cleanup after download (optional, but good hygiene)
        # For now, we keep them.
        return FileResponse(
            path=result["path"], 
            filename=result["filename"], 
            media_type='application/zip'
        )
    return {"error": "Failed to create pack"}

from services.event_stream import EventStreamGenerator
from services.system_info import get_system_snapshot, get_driver_changes
from fastapi.responses import StreamingResponse

# ...

@app.get("/api/events/stream")
async def event_stream():
    """
    Live stream of system events via SSE.
    """
    gen = EventStreamGenerator()
    # StreamingResponse with proper headers for SSE
    return StreamingResponse(
        gen.event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )

@app.get("/api/system/snapshot")
async def api_system_snapshot():
    """
    Cached system snapshot (CPU, RAM, Uptime).
    """
    return await get_system_snapshot()

@app.get("/api/system/driver-changes")
async def api_driver_changes():
    """
    Cached list of recent driver changes/installs.
    """
    return await get_driver_changes()

from services.deep_diagnostics import get_deep_diagnostics

@app.get("/api/system/diagnostics")
async def api_deep_diagnostics(target_time: str = None):
    """
    Diagnostic Grade Hardware Context (Slow Scan).
    """
    return await get_deep_diagnostics(target_time)

# Legacy Endpoints (Keep for now or deprecate)
@app.get("/api/logs/system")
async def read_system_logs(count: int = 10):
    res = EventCollector().collect()
    return res.data

@app.get("/api/logs/whea")
async def read_whea_logs(count: int = 20):
    res = WheaCollector().collect()
    return res.data

from services.system_logs import get_events_prior_to_timestamp
from pydantic import BaseModel

class ContextRequest(BaseModel):
    timestamp: str
    count: int = 50

@app.post("/api/logs/context")
async def get_context_logs(req: ContextRequest):
    """
    Fetch events immediately preceding a specific timestamp.
    Used for "Pre-Crash Context" to bridge silent freeze gaps.
    """
    return get_events_prior_to_timestamp(req.timestamp, req.count)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
