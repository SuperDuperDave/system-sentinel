import asyncio
import logging
import datetime
from .domains.hardware import get_hardware_overview
from .domains.pcie import get_pcie_fabric_context
from .domains.power import get_power_context
from .domains.memory import get_memory_context
from .domains.constraints import get_constraints_context
from .domains.forensic_signals import get_forensic_signals_context

logger = logging.getLogger(__name__)

async def get_deep_diagnostics(target_time=None):
    """
    Main entry point for fetching the complete diagnostic report.
    Aggregates data from all forensic domains.
    """
    try:
        # Define tasks as a dictionary for easier lookup when gathering results
        task_definitions = {
            "hardware": get_hardware_overview(),
            "pcie": get_pcie_fabric_context(),
            "power": get_power_context(),
            "memory": get_memory_context(),
            "constraints": get_constraints_context(),
            "forensic_signals": get_forensic_signals_context()
        }
        
        domain_names = list(task_definitions.keys())
        domain_tasks = list(task_definitions.values())
        
        # Parallel execution with return_exceptions=True to prevent a single domain crash from killing the report
        results = await asyncio.gather(*domain_tasks, return_exceptions=True)
        
        report = {
            "timestamp": datetime.datetime.now().isoformat(),
            "version": "1.1.0",
            "domains": {}
        }
        
        for name, result in zip(domain_names, results):
            if isinstance(result, Exception):
                logger.error(f"Error gathering diagnostics for domain '{name}': {result}")
                report["domains"][name] = {"error": str(result)}
            else:
                report["domains"][name] = result or {}
                
        return report

    except Exception as e:
        logger.exception(f"Fatal exception in full diagnostic run: {e}")
        return {
            "error": "Internal diagnostic engine failure",
            "details": str(e),
            "timestamp": datetime.datetime.now().isoformat()
        }
