"""Luna diagnostics package for health monitoring and debugging."""

from .health import HealthChecker, HealthCheck, HealthStatus
from .critical_systems import CriticalSystemsCheck, run_startup_check
from .startup_checks import StartupChecks, CheckStatus, CheckResult, run_startup_checks
from .watchdog import LunaWatchdog, WatchdogAlert, get_watchdog, start_watchdog

__all__ = [
    # Health checks
    'HealthChecker',
    'HealthCheck',
    'HealthStatus',
    # Critical systems gate (sync, strict)
    'CriticalSystemsCheck',
    'run_startup_check',
    # Async startup checks (configurable)
    'StartupChecks',
    'CheckStatus',
    'CheckResult',
    'run_startup_checks',
    # Runtime watchdog
    'LunaWatchdog',
    'WatchdogAlert',
    'get_watchdog',
    'start_watchdog',
]
