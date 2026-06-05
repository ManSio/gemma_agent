"""Critical Decision Center — фаза 1: события хода, агрегаты, политика tier/module."""

from core.cdc.engine import (
    apply_route_tier_cap,
    cdc_enabled,
    maybe_apply_planner_penalty,
    process_turn_end,
)

__all__ = [
    "apply_route_tier_cap",
    "cdc_enabled",
    "maybe_apply_planner_penalty",
    "process_turn_end",
]
