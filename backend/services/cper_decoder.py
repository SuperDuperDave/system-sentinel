import subprocess
import logging
import os
import json
from tempfile import NamedTemporaryFile

logger = logging.getLogger(__name__)

import os

# Use relative path from the project root (assuming execution from backend dir)
# or resolve absolute path based on __file__
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TOOL_PATH = os.path.join(BASE_DIR, "tools", "DecodeWheaRecord", "DecodeWheaRecord.exe")

class WheaDecoderService:
    def __init__(self, tool_path=DEFAULT_TOOL_PATH):
        self.tool_path = tool_path

    def decode_cper_hex(self, record_id: str, hex_data: str) -> dict:
        """
        Decodes a hex string (CPER) into a JSON structure using the external tool.
        """
        # Debug logging to see what we are actually getting
        logger.info(f"[Record {record_id}] Raw hex input length: {len(hex_data)}")
        logger.info(f"[Record {record_id}] Raw hex input sample: {hex_data[:50]}...")

        clean_hex = hex_data.replace("-", "").replace(" ", "").replace("\n", "").replace("\r", "").strip()
        
        logger.info(f"[Record {record_id}] Cleaned hex length: {len(clean_hex)}")

        if not clean_hex:
            return {"error": "Empty hex data"}

        try:
            # The tool seemingly expects the hex string as the first argument
            cmd = [self.tool_path, clean_hex]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                # Common issue: Command line length limit on Windows (32k chars). 
                # CPER records can be large. If this becomes an issue, we'll need to check if tool 
                # supports file input or stdin. But based on error "Hexadecimal string...", it wants args.
                logger.warning(f"Decoding failed for Record {record_id}: {result.stderr}")
                return {"error": "Tool execution failed", "stderr": result.stderr}
            
            try:
                # Try parsing stdout as JSON
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                # Loop fallback: maybe it output text?
                return {"raw_stdout": result.stdout}

        except Exception as e:
            logger.error(f"Decoder exception: {e}")
            return {"error": str(e)}
