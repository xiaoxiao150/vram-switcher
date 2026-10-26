"""VRAM Switcher core package.

The package is deliberately free of AstrBot imports so it can be tested and
reused as a small local-GPU task coordinator.  ``main.py`` is the AstrBot
adapter.
"""

from .config import ConfigError, VramConfig, load_config
from .orchestrator import NullProviderHooks, Orchestrator
from .system import ProcessInfo, SystemAdapter
from .triggers import TriggerMatcher

__all__ = [
    "ConfigError",
    "VramConfig",
    "load_config",
    "NullProviderHooks",
    "Orchestrator",
    "ProcessInfo",
    "SystemAdapter",
    "TriggerMatcher",
]
