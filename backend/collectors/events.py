from .base import BaseCollector, CollectorResult
from services.system_logs import get_recent_critical_events, get_whea_events
from services.cper_decoder import WheaDecoderService
import logging

logger = logging.getLogger(__name__)

class EventCollector(BaseCollector):
    def __init__(self):
        super().__init__(
            collector_id="events.critical",
            name="Critical System Events",
            description="Collects recent critical/error level logs from Windows Event Viewer (System log)"
        )

    def collect(self) -> CollectorResult:
        try:
            data = get_recent_critical_events(max_events=20)
            return CollectorResult(self.collector_id, data)
        except Exception as e:
            logger.error(f"Error in EventCollector: {e}")
            return CollectorResult(self.collector_id, [], str(e))

class WheaCollector(BaseCollector):
    def __init__(self):
        super().__init__(
            collector_id="events.whea",
            name="WHEA Hardware Errors",
            description="Collects Hardware Error events from Microsoft-Windows-WHEA-Logger"
        )

    def collect(self) -> CollectorResult:
        try:
            raw_events = get_whea_events(max_events=30)
            
            # Enhancement: Attempt to decode if RawData is present
            decoder = WheaDecoderService()
            enhanced_events = []
            
            for event in raw_events:
                # Check for binary data blob
                if "RawData" in event and event["RawData"]:
                    decoded = decoder.decode_cper_hex(str(event.get("Id", "?")), event["RawData"])
                    event["DecodedCPER"] = decoded
                enhanced_events.append(event)
                
            return CollectorResult(self.collector_id, enhanced_events)
        except Exception as e:
            logger.error(f"Error in WheaCollector: {e}")
            return CollectorResult(self.collector_id, [], str(e))
