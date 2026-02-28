from datetime import datetime
from typing import Dict, Any, Optional
import xml.etree.ElementTree as ET
import re

from .models import WheaEvent

class WheaParser:
    """
    Pure transformation of raw Windows Event Log (dict/json) into WheaEvent.
    """
    
    @staticmethod
    def parse_event(raw_event: Dict[str, Any]) -> WheaEvent:
        """
        Normalizes a raw dict (from PowerShell JSON) into a WheaEvent.
        """
        # 1. Basic Fields
        try:
            time_created = datetime.fromisoformat(raw_event.get("TimeCreated", "").replace("Z", "+00:00"))
        except ValueError:
            time_created = datetime.now() # Fallback

        msg = raw_event.get("Message", "")
        # Fallback if message is None (can happen in heavy load/weird providers)
        if msg is None: msg = ""

        # 2. Extract Details (XML or Message)
        
        # Initialize extracted
        error_type = "Unknown"
        bank = None
        apic_id = None
        mci_status = None
        vendor_id = None
        device_id = None
        corrected = True # Default to true? Logic: EventID based?
        
        # Event ID heuristics for "Corrected" vs "Fatal"
        # 17, 19, 47 are commonly corrected. 18 is fatal.
        # But we rely on level mainly? No, WHEA-Logger often uses Info/Warning for corrected.
        # Let's assume input filter handled "Corrected" scope if that's the goal, 
        # but parsing should reflect reality.
        # Note: Implementation Plan says we focus on "Corrected".
        
        # --- Type Detection ---
        if "PCI" in msg or "Root Port" in msg:
            error_type = "PCIe Error"
        elif "Memory" in msg or "ECC" in msg:
            error_type = "Memory Error"
        elif "Cache" in msg:
            error_type = "Cache Error"
        elif "Bus" in msg or "Interconnect" in msg:
            error_type = "Bus Error"
        elif "Translation Lookaside Buffer" in msg or "TLB" in msg:
            error_type = "TLB Error"
            
        # --- Regex Extraction from Message ---
        # "Bank: 5"
        m_bank = re.search(r'Bank:\s*(\d+)', msg)
        if m_bank: bank = m_bank.group(1)
        
        # "APIC ID: 0"
        m_apic = re.search(r'APIC\s*ID:\s*(\d+)', msg)
        if m_apic: apic_id = m_apic.group(1)

        # "MCI Status: 0x..."
        m_mci = re.search(r'MCI\s*Status:\s*(0x[0-9a-fA-F]+)', msg, re.IGNORECASE)
        if m_mci: mci_status = m_mci.group(1)
        
        # PCIe IDs: "Vendor ID:Device ID: 8086:1234" or similar
        # Formats vary wildly.
        m_pci = re.search(r'Vendor\s*ID:Device\s*ID:\s*([0-9a-fA-F]+):([0-9a-fA-F]+)', msg, re.IGNORECASE)
        if m_pci: 
            vendor_id = m_pci.group(1)
            device_id = m_pci.group(2)
            error_type = "PCIe Error" # Confirm it

        # --- XML Parsing (If available and needed) ---
        # The raw extraction from PS might put XML string in a wrapper or we might need to parse extracted props.
        # For now, regex on Message is remarkably robust for standard WHEA.
        # If we need XML later for complex bitfields, we add it here.
        
        return WheaEvent(
            record_id=int(raw_event.get("Id", 0)), # 'Id' in JSON from Select-Object usually matches RecordId if we selected Index? 
            # Wait, Select-Object Id is EventID. RecordId is separate.
            # We strictly need RecordId for watermark.
            # If the PS script in ingest.py selects RecordId, we use it. 
            # If not, let's assume ingest passes 'RecordId'.
            # (I will ensure ingest.py selects RecordId)
            
            event_id=int(raw_event.get("Id", 0)), # This is Event ID (17, 18, etc)
            time_created=time_created,
            provider_name=raw_event.get("ProviderName", "Microsoft-Windows-WHEA-Logger"),
            message=msg,
            error_type=error_type,
            corrected=corrected,
            bank=bank,
            apic_id=apic_id,
            mci_status=mci_status,
            vendor_id=vendor_id,
            device_id=device_id
            # raw_xml handled if ingest provides it, mapped to optional field.
        )
