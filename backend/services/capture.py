import os
import shutil
import json
import datetime
import zipfile
from tempfile import TemporaryDirectory
from collectors.base import BaseCollector, CollectorResult
from collectors.events import EventCollector, WheaCollector
from collectors.dumps import DumpCollector
from collectors.system import SystemCollector

class CapturePackService:
    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    async def create_pack(self) -> dict:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        pack_name = f"SystemSentinel_Capture_{timestamp}"
        
        # Use a temp dir to assemble
        with TemporaryDirectory() as tmp_dir:
            base_path = os.path.join(tmp_dir, pack_name)
            os.makedirs(base_path)

            # 1. Collect Data
            events = EventCollector().collect().data
            whea = WheaCollector().collect().data
            dumps = DumpCollector().collect().data
            system = SystemCollector().collect().data
            
            manifest = {
                "timestamp": datetime.datetime.now().isoformat(),
                "contents": ["events.json", "whea.json", "dumps_inventory.json", "system.json"]
            }

            # 2. Write JSONs
            with open(os.path.join(base_path, "events.json"), "w") as f:
                json.dump(events, f, indent=2)
            with open(os.path.join(base_path, "whea.json"), "w") as f:
                json.dump(whea, f, indent=2)
            with open(os.path.join(base_path, "dumps_inventory.json"), "w") as f:
                json.dump(dumps, f, indent=2)
            with open(os.path.join(base_path, "system.json"), "w") as f:
                json.dump(system, f, indent=2)
            
            with open(os.path.join(base_path, "manifest.json"), "w") as f:
                json.dump(manifest, f, indent=2)

            # 3. Zip it up
            zip_filename = f"{pack_name}.zip"
            zip_path = os.path.join(self.output_dir, zip_filename)
            
            shutil.make_archive(os.path.join(self.output_dir, pack_name), 'zip', tmp_dir, pack_name)
            
            return {
                "success": True,
                "path": zip_path,
                "filename": zip_filename,
                "size_bytes": os.path.getsize(zip_path)
            }

