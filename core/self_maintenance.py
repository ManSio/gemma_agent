from __future__ import annotations

import logging

import os
import time
from typing import Any, Dict

from core.data_governance import DG
from core.error_analysis import aggregate_error_stats, record_error_event
from core.system_housekeeping import housekeeping_enabled, run_housekeeping


logger = logging.getLogger(__name__)

class SelfMaintenanceCycles:
    """Background-safe maintenance cycle runner (advisory + hygiene).

    Skips if any user activity within MAINTENANCE_IDLE_MIN_SEC seconds
    (default 300 = 5 minutes) to avoid latency spikes during active use.
    """

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = bool(enabled)
        # С момента старта процесса, а не «первый plan()»: иначе первое сообщение
        # синхронно тянет purge + recovery tick + resilience.tick (десятки секунд на слабом диске/при critical).
        self.last_run_ts = time.monotonic()

    def maybe_run(self, *, interval_sec: float = 600.0) -> Dict[str, Any]:
        if not self.enabled:
            return {"ran": False, "reason": "disabled"}
        # Skip maintenance if any user activity within idle window
        try:
            from core.usage_learning import seconds_since_activity
            min_idle = max(30, int(os.getenv("MAINTENANCE_IDLE_MIN_SEC", "300")))
            if seconds_since_activity() < float(min_idle):
                return {"ran": False, "reason": "users_active"}
        except Exception as e:
            logger.debug('%s optional failed: %s', 'self_maintenance', e, exc_info=True)
        now = time.monotonic()
        if self.last_run_ts and (now - self.last_run_ts) < max(30.0, interval_sec):
            return {"ran": False, "reason": "interval_not_reached"}
        self.last_run_ts = now
        try:
            purge = DG.purge_runtime_logs()
            diag = aggregate_error_stats(limit=200)
            kv_sweep: Dict[str, Any] = {}
            try:
                from core.agent_kv.policy import sweep_agent_kv

                kv_sweep = sweep_agent_kv()
            except Exception as e:
                logger.debug('%s optional failed: %s', 'self_maintenance', e, exc_info=True)
            housekeeping: Dict[str, Any] = {}
            try:
                if housekeeping_enabled():
                    housekeeping = run_housekeeping(dry_run=False)
            except Exception as e:
                logger.debug('%s optional failed: %s', 'self_maintenance', e, exc_info=True)
            return {
                "ran": True,
                "purge": purge,
                "error_diagnostics": diag,
                "agent_kv_sweep": kv_sweep,
                "housekeeping": housekeeping,
            }
        except Exception as e:
            record_error_event("self_maintenance", "maintenance cycle failed", exc=e)
            return {"ran": False, "error": str(e)}
