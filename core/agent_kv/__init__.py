"""
Персистентный Agent KV: CDC, репутация, стратегии, маршрутизатор, grim-trigger,
TTL, приоритеты, ветки, rollback по истории версий.
"""

from core.agent_kv.grim import (
    apply_grim_to_policy,
    hydrate_cdc_from_kv,
    merge_grim_policy_into,
    update_grim_after_turn,
)
from core.affect_state import (
    affect_enabled,
    default_affect_state,
    hydrate_affect_from_kv,
    modulate_task_tier_with_affect,
    update_affect_after_turn,
)
from core.agent_kv.policy import sweep_agent_kv
from core.agent_kv.router_stats import record_router_turn
from core.agent_kv.store import (
    agent_kv_branch,
    agent_kv_enabled,
    copy_branch,
    default_kv_path,
    delete_key,
    get_json,
    get_history,
    iter_prefix,
    list_branches,
    rollback_to_version,
    set_json,
)

__all__ = [
    "agent_kv_branch",
    "agent_kv_enabled",
    "apply_grim_to_policy",
    "hydrate_cdc_from_kv",
    "copy_branch",
    "default_kv_path",
    "delete_key",
    "get_history",
    "get_json",
    "iter_prefix",
    "list_branches",
    "merge_grim_policy_into",
    "record_router_turn",
    "rollback_to_version",
    "set_json",
    "sweep_agent_kv",
    "update_grim_after_turn",
    "affect_enabled",
    "default_affect_state",
    "hydrate_affect_from_kv",
    "modulate_task_tier_with_affect",
    "update_affect_after_turn",
]
