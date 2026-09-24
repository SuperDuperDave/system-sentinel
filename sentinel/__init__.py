"""System Sentinel: a stethoscope for a Windows computer.

One boundary (the API), three clients (the dashboard, a phone, a local agent).
Every read of the machine passes through :mod:`sentinel.bridge`; every result
is a :class:`sentinel.reading.Reading` whose outcome says whether the machine
was observed at all.
"""

__version__ = "1.9.22"
