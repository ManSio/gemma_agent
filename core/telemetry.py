"""
Telemetry and Logging - Улучшенное логирование и телеметрия.
Autonomy 3.0: self-optimization metrics (response time, error count,
tool count, reasoning depth, tool-chain success rate).
"""
import logging
import time
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta
from core.event_bus import bus
from core.models import Input, Output

SELF_OPTIMIZATION_VERSION = "2.0.0"

_WINDOW_SEC = 600  # 10-minute metric aggregation window


class TelemetryLogger:
    """Журнал телеметрии и логирования"""

    def __init__(self):
        self.logger = logging.getLogger("Telemetry")
        self.metrics = {
            "module_calls": {},
            "errors": [],
            "latency": [],
            "active_users": set(),
        }
        # Autonomy 3.0 self-optimization stats
        self._response_times: List[Tuple[float, str]] = []
        self._tool_counts: List[int] = []
        self._reasoning_depths: List[str] = []
        self._tool_chain_outcomes: List[bool] = []
    
    def log_module_call(self, module_name: str, input_data: Input, response_time: float):
        """Логировать вызов модуля"""
        try:
            # Фиксируем вызов модуля
            if module_name not in self.metrics["module_calls"]:
                self.metrics["module_calls"][module_name] = {
                    "count": 0,
                    "total_latency": 0,
                    "first_call": datetime.now()
                }
            
            self.metrics["module_calls"][module_name]["count"] += 1
            self.metrics["module_calls"][module_name]["total_latency"] += response_time
            
            # Логируем вызов
            self.logger.info(
                f"Module call: {module_name}, "
                f"input_type: {input_data.type}, "
                f"response_time: {response_time:.2f}ms"
            )
            
            # Вызываем событие (fire-and-forget)
            bus.emit_ff("module.called", {
                "module": module_name,
                "input_type": input_data.type,
                "response_time": response_time,
                "timestamp": datetime.now().isoformat()
            })
            
        except Exception as e:
            self.logger.error(f"Error logging module call: {e}")
    
    def log_error(self, module_name: str, error: str, input_data: Input = None):
        """Логировать ошибки"""
        error_info = {
            "module": module_name,
            "error": error,
            "timestamp": datetime.now().isoformat()
        }
        
        if input_data:
            error_info["input_type"] = input_data.type
            error_info["input_payload"] = str(input_data.payload)[:100]  # Ограничиваем размер
        
        self.metrics["errors"].append(error_info)
        
        # Логируем ошибку
        self.logger.error(f"Module error: {module_name} - {error}")
        
        # Вызываем событие (fire-and-forget)
        bus.emit_ff("module.failed", {
            "module": module_name,
            "error": error,
            "timestamp": datetime.now().isoformat()
        })
    
    def log_user_activity(self, user_id: str, action: str, details: Dict[str, Any] = None):
        """Логировать активность пользователя"""
        self.metrics["active_users"].add(user_id)
        
        self.logger.info(
            f"User activity: {user_id} - {action} - {details or {}}"
        )
    
    def get_module_metrics(self) -> Dict[str, Any]:
        """Получить метрики модулей"""
        module_metrics = {}
        for module_name, stats in self.metrics["module_calls"].items():
            avg_latency = stats["total_latency"] / stats["count"] if stats["count"] > 0 else 0
            module_metrics[module_name] = {
                "call_count": stats["count"],
                "average_latency": avg_latency,
                "first_call": stats["first_call"].isoformat()
            }
        return module_metrics
    
    def get_error_metrics(self) -> Dict[str, Any]:
        """Получить метрики ошибок"""
        return {
            "error_count": len(self.metrics["errors"]),
            "errors": self.metrics["errors"][-50:]  # Последние 50 ошибок
        }
    
    def get_usage_metrics(self) -> Dict[str, Any]:
        """Получить метрики использования"""
        return {
            "active_users_count": len(self.metrics["active_users"]),
            "module_metrics": self.get_module_metrics(),
            "error_metrics": self.get_error_metrics()
        }

    # ── Autonomy 3.0 Self-Optimization Metrics ──

    def record_response_time(self, seconds: float, tag: str = "") -> None:
        self._response_times.append((time.time(), seconds))
        if len(self._response_times) > 500:
            self._response_times = self._response_times[-300:]

    def record_tool_count(self, count: int) -> None:
        self._tool_counts.append(count)
        if len(self._tool_counts) > 500:
            self._tool_counts = self._tool_counts[-300:]

    def record_reasoning_depth(self, depth: str) -> None:
        self._reasoning_depths.append(depth)
        if len(self._reasoning_depths) > 200:
            self._reasoning_depths = self._reasoning_depths[-100:]

    def record_tool_chain_outcome(self, success: bool) -> None:
        self._tool_chain_outcomes.append(success)
        if len(self._tool_chain_outcomes) > 500:
            self._tool_chain_outcomes = self._tool_chain_outcomes[-300:]

    def analyze_optimization(self) -> Dict[str, Any]:
        """Analyze metrics and return optimization suggestions."""
        now = time.time()
        recent_rts = [(ts, v) for ts, v in self._response_times if now - ts <= _WINDOW_SEC]
        avg_rt = sum(v for _, v in recent_rts) / len(recent_rts) if recent_rts else 0.0

        slow_chains = []
        if avg_rt > 5.0:
            slow_chains.append("response_time_high")
        if avg_rt > 15.0:
            slow_chains.append("response_time_critical")

        total_errs = len(self.metrics.get("errors", []))
        if total_errs > 10:
            slow_chains.append("error_rate_high")

        recent_depths = [d for d in self._reasoning_depths[-20:]] if self._reasoning_depths else []
        deep_count = sum(1 for d in recent_depths if d == "deep")
        if deep_count > 10:
            slow_chains.append("reduce_reasoning_depth")

        recent_outcomes = self._tool_chain_outcomes[-30:] if self._tool_chain_outcomes else []
        if recent_outcomes:
            success_rate = sum(1 for o in recent_outcomes if o) / len(recent_outcomes)
            if success_rate < 0.4:
                slow_chains.append("inefficient_tool_chain")

        return {
            "avg_response_time_sec": round(avg_rt, 3),
            "error_count": total_errs,
            "deep_reasoning_ratio": deep_count / max(len(recent_depths), 1),
            "tool_chain_success_rate": round(
                sum(1 for o in recent_outcomes if o) / max(len(recent_outcomes), 1), 3
            ) if recent_outcomes else 1.0,
            "optimizations": slow_chains,
        }

    def build_optimization_hint(self) -> Optional[str]:
        """Build a compact hint for auto-optimization."""
        analysis = self.analyze_optimization()
        opts = analysis.get("optimizations") or []
        if not opts:
            return None
        hints = []
        if "reduce_reasoning_depth" in opts:
            hints.append("depth:shallow")
        if "inefficient_tool_chain" in opts:
            hints.append("tools:minimal")
        if "response_time_critical" in opts:
            hints.append("trim:aggressive")
        return ",".join(hints) if hints else None

    def record_token_budget(
        self,
        est_tokens: int,
        real_tokens_prompt: int,
        real_tokens_completion: int,
        tokens_cached: int,
        budget_limit: int,
        exceeded: bool,
    ) -> None:
        """Record token budget telemetry for prompt budgeting."""
        self.logger.info(
            "token_budget est=%d real_prompt=%d real_completion=%d cached=%d limit=%d exceeded=%s",
            est_tokens,
            real_tokens_prompt,
            real_tokens_completion,
            tokens_cached,
            budget_limit,
            exceeded,
        )


# Создаем глобальный инстанс
telemetry_logger = TelemetryLogger()