from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
import datetime

class CollectorResult:
    def __init__(self, collector_id: str, data: Any, error: Optional[str] = None):
        self.collector_id = collector_id
        self.timestamp = datetime.datetime.now().isoformat()
        self.data = data
        self.error = error

class BaseCollector(ABC):
    def __init__(self, collector_id: str, name: str, description: str):
        self.collector_id = collector_id
        self.name = name
        self.description = description

    @abstractmethod
    def collect(self) -> CollectorResult:
        """
        Execute collection logic and return a CollectorResult.
        """
        pass
