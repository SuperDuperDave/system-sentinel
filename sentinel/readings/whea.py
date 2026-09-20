"""Hardware errors: ``whea`` (the WHEA-Logger records with their payload decoded beside them)
and ``storms`` (the records over a window in wall-clock buckets, by signature, with burst and
acceleration flags).

Built in phase 2 against docs/API.md from the queries and rules in the old backend
(``backend/services/system_logs.py:get_whea_events``, ``backend/services/cper_decoder.py``,
``backend/services/whea/{parse,aggregate,storms}.py``). The decoder lives at
``sentinel/tools/DecodeWheaRecord/DecodeWheaRecord.exe``.
"""
