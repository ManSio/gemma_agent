from __future__ import annotations

import logging

import os
from dataclasses import dataclass
from typing import Dict


logger = logging.getLogger(__name__)

def _b(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    s = raw.strip()
    if not s:
        return default
    return s.lower() in {"1", "true", "yes", "on"}


@dataclass
class AppConfig:
    anti_flood_enabled: bool = _b("ANTI_FLOOD_ENABLED", True)
    link_safety_enabled: bool = _b("LINK_SAFETY_ENABLED", True)
    file_intake_enabled: bool = _b("FILE_INTAKE_ENABLED", True)
    image_tools_enabled: bool = _b("IMAGE_TOOLS_ENABLED", True)
    voice_enabled: bool = _b("VOICE_ENABLED", False)
    max_msg_per_10s: int = int(os.getenv("MAX_MSG_PER_10S", "7"))
    max_same_text: int = int(os.getenv("MAX_SAME_TEXT", "3"))
    max_cmd_per_10s: int = int(os.getenv("MAX_CMD_PER_10S", "4"))
    group_cooldown_sec: float = float(os.getenv("GROUP_COOLDOWN_SEC", "2.0"))
    op_timeout_sec: float = float(os.getenv("OP_TIMEOUT_SEC", "20"))
    op_retries: int = int(os.getenv("OP_RETRIES", "2"))
    image_max_resolution: int = int(os.getenv("IMAGE_MAX_RESOLUTION", "2048"))
    heavy_worker_enabled: bool = _b("HEAVY_WORKER_ENABLED", True)
    heavy_worker_concurrency: int = int(os.getenv("HEAVY_WORKER_CONCURRENCY", "2"))
    heavy_worker_queue_max: int = int(os.getenv("HEAVY_WORKER_QUEUE_MAX", "100"))
    heavy_worker_timeout_sec: float = float(os.getenv("HEAVY_WORKER_TIMEOUT_SEC", "60"))
    predictive_behavior_enabled: bool = _b("PREDICTIVE_BEHAVIOR_ENABLED", True)
    predictive_confidence_threshold: float = float(os.getenv("PREDICTIVE_CONFIDENCE_THRESHOLD", "0.6"))
    goal_engine_enabled: bool = _b("GOAL_ENGINE_ENABLED", True)
    self_maintenance_enabled: bool = _b("SELF_MAINTENANCE_ENABLED", True)
    # 1200 с = 20 мин: реже тяжёлый plan(), чем 600 (10 мин) — меньше «залипаний» на слабом диске/VPS
    self_maintenance_interval_sec: float = float(os.getenv("SELF_MAINTENANCE_INTERVAL_SEC", "1200"))
    self_improvement_advisor_enabled: bool = _b("SELF_IMPROVEMENT_ADVISOR_ENABLED", True)

    def as_dict(self) -> Dict[str, object]:
        return {
            "anti_flood_enabled": self.anti_flood_enabled,
            "link_safety_enabled": self.link_safety_enabled,
            "file_intake_enabled": self.file_intake_enabled,
            "image_tools_enabled": self.image_tools_enabled,
            "voice_enabled": self.voice_enabled,
            "max_msg_per_10s": self.max_msg_per_10s,
            "max_same_text": self.max_same_text,
            "max_cmd_per_10s": self.max_cmd_per_10s,
            "group_cooldown_sec": self.group_cooldown_sec,
            "op_timeout_sec": self.op_timeout_sec,
            "op_retries": self.op_retries,
            "image_max_resolution": self.image_max_resolution,
            "heavy_worker_enabled": self.heavy_worker_enabled,
            "heavy_worker_concurrency": self.heavy_worker_concurrency,
            "heavy_worker_queue_max": self.heavy_worker_queue_max,
            "heavy_worker_timeout_sec": self.heavy_worker_timeout_sec,
            "predictive_behavior_enabled": self.predictive_behavior_enabled,
            "predictive_confidence_threshold": self.predictive_confidence_threshold,
            "goal_engine_enabled": self.goal_engine_enabled,
            "self_maintenance_enabled": self.self_maintenance_enabled,
            "self_maintenance_interval_sec": self.self_maintenance_interval_sec,
            "self_improvement_advisor_enabled": self.self_improvement_advisor_enabled,
        }

    def validate(self) -> Dict[str, object]:
        errors = []
        warnings = []
        if self.max_msg_per_10s < 1:
            errors.append("MAX_MSG_PER_10S must be >= 1")
        if self.max_same_text < 1:
            errors.append("MAX_SAME_TEXT must be >= 1")
        if self.max_cmd_per_10s < 1:
            errors.append("MAX_CMD_PER_10S must be >= 1")
        if self.group_cooldown_sec < 0:
            errors.append("GROUP_COOLDOWN_SEC must be >= 0")
        if self.op_timeout_sec <= 0:
            errors.append("OP_TIMEOUT_SEC must be > 0")
        if self.op_retries < 0:
            errors.append("OP_RETRIES must be >= 0")
        if self.image_max_resolution < 256:
            warnings.append("IMAGE_MAX_RESOLUTION is very low (<256)")
        if self.heavy_worker_concurrency < 1:
            errors.append("HEAVY_WORKER_CONCURRENCY must be >= 1")
        if self.heavy_worker_queue_max < 10:
            warnings.append("HEAVY_WORKER_QUEUE_MAX is small; tasks may be dropped under load")
        if not (0.0 <= self.predictive_confidence_threshold <= 1.0):
            errors.append("PREDICTIVE_CONFIDENCE_THRESHOLD must be between 0 and 1")
        if self.self_maintenance_interval_sec < 30:
            warnings.append("SELF_MAINTENANCE_INTERVAL_SEC is very low (<30)")
        try:
            from core.voice_module import VoiceModule

            vm = VoiceModule()
            if vm.enabled and vm.stt_enabled:
                b = vm.stt_backend
                if b in {"openai", "whisper", "whisper-1", "api"}:
                    if not (os.getenv("VOICE_STT_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip():
                        warnings.append("VOICE/STT: openai backend без VOICE_STT_API_KEY или OPENAI_API_KEY")
                elif b in {"openrouter", "or"}:
                    if not os.getenv("OPENROUTER_API_KEY", "").strip():
                        warnings.append("VOICE/STT: openrouter backend без OPENROUTER_API_KEY")
                elif b == "vosk" and not (os.getenv("VOICE_STT_MODEL_PATH") or "").strip():
                    warnings.append(
                        "VOICE/STT: vosk без VOICE_STT_MODEL_PATH — укажите каталог модели (alphacephei.com/vosk/models) или whisper.cpp"
                    )
                fb_env = (os.getenv("VOICE_STT_FALLBACK_BACKEND") or "").strip().lower()
                fb_eff = (getattr(vm, "_stt_fallback", None) or "").strip().lower()
                if vm.stt_local_only and fb_env in {"openrouter", "or", "openai", "whisper", "whisper-1", "api"}:
                    warnings.append(
                        "VOICE_STT_LOCAL_ONLY: облачный fallback в .env задан, но не применяется (только локальный STT)"
                    )
                if fb_eff in {"openrouter", "or"} and not os.getenv("OPENROUTER_API_KEY", "").strip():
                    warnings.append("VOICE/STT: fallback openrouter без OPENROUTER_API_KEY")
                if fb_eff in {"openai", "whisper", "whisper-1", "api"} and not (
                    os.getenv("VOICE_STT_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
                ).strip():
                    warnings.append("VOICE/STT: fallback openai без ключа")
        except Exception as e:
            logger.debug('%s optional failed: %s', 'config_manager', e, exc_info=True)
        return {"ok": len(errors) == 0, "errors": errors, "warnings": warnings}


_CONFIG = AppConfig()


def get_config() -> AppConfig:
    return _CONFIG
