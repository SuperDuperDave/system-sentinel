import hashlib
import re
from typing import Tuple, Optional
from .models import WheaEvent

class WheaSignatureGenerator:
    """
    Computes stable signatures for WHEA events using a tiered strategy.
    
    Versioning:
    - v1: Initial Strategy (Stable Feature -> PCIe -> Fallback)
    """
    VERSION = "v1"

    @staticmethod
    def compute_signature(event: WheaEvent) -> Tuple[str, str, str]:
        """
        Returns (signature_id, signature_key, description).
        
        signature_key: Human readable components.
        signature_id: Hashed identifier.
        """
        
        components = [WheaSignatureGenerator.VERSION]
        
        # Tier 1: Stable Hardware Features
        components.append(f"ET:{event.error_type}")
        
        if event.bank:
            components.append(f"BK:{event.bank}")
        
        if event.apic_id:
            components.append(f"APIC:{event.apic_id}")
            
        if event.mci_status:
            components.append(f"MCI:{event.mci_status}")

        # Tier 2: PCIe Specifics
        # If we have vendor/device, use them as they are very stable
        if event.vendor_id and event.device_id:
            components.append(f"PCI:{event.vendor_id}:{event.device_id}")
        elif event.message and "PCI" in event.message:
             # If no IDs but message says PCI, try to extract BDF from message if parser missed it?
             # For now, rely on what's in extracted fields
             pass
             
        # Tier 3: Fallback (Normalized Message)
        # Use this ONLY if we extracted very little info (e.g. just "Unknown" error type)
        # OR as a disambiguator if multiple distinct errors map to same bank/type.
        # Let's ALWAYS append a normalized snippet to capture nuances not in fields, 
        # BUT aggressively normalize to avoid noise (timestamps, pointer addresses).
        
        norm_msg = WheaSignatureGenerator._normalize_message(event.message)
        components.append(f"MSG:{norm_msg}")

        # Finalize
        signature_key = "|".join(components)
        signature_id = hashlib.sha256(signature_key.encode('utf-8')).hexdigest()
        
        # Generate Description
        description = f"{event.error_type}"
        if event.bank: description += f" (Bank {event.bank})"
        if event.apic_id: description += f" (APIC {event.apic_id})"
        if event.vendor_id: description += f" [PCI {event.vendor_id}:{event.device_id}]"
        
        return signature_id, signature_key, description

    @staticmethod
    def _normalize_message(msg: str) -> str:
        if not msg: return ""
        
        # 1. Remove localized "Corrected hardware error has occurred." prefixes if consistent?
        # Actually WHEA messages are often just that.
        
        # 2. Strip Hex Addresses (0x1234AB...)
        msg = re.sub(r'0x[0-9a-fA-F]+', '<HEX>', msg)
        
        # 3. Strip GUIDs
        msg = re.sub(r'\{[0-9a-fA-F-]{36}\}', '<GUID>', msg)
        
        # 4. Strip excessive whitespace
        msg = " ".join(msg.split())
        
        # 5. Take first N chars to avoid super long variation at tail
        return msg[:100]
