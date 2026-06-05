"""
Модели данных для ядра
"""

from typing import Any, Dict, Literal, Optional
from pydantic import BaseModel, Field
from datetime import datetime

class Input(BaseModel):
    type: Literal["text", "image", "audio", "video", "file"]
    payload: Any  # текст или байты/ссылка
    meta: Dict = Field(default_factory=dict)  # user_id, chat_id, message_id, source, timestamps


class User(BaseModel):
    """Минимальный пользователь для SecurityLayer.check_permission (не ORM core.database.User)."""

    id: Optional[str] = None
    external_id: Optional[str] = None
    name: str = ""
    username: Optional[str] = None
    role: str = "child"


class Output(BaseModel):
    type: Literal["text", "image", "audio", "file"]
    payload: Any
    meta: Dict = Field(default_factory=dict)


class PlanStep(BaseModel):
    module_name: str
    args: Dict = Field(default_factory=dict)


class Plan(BaseModel):
    steps: list[PlanStep]
    mode: Literal["full", "degraded", "emergency"]


class ModuleState(BaseModel):
    name: str
    type: str
    status: Literal["healthy", "degraded", "failed", "disabled"]
    last_error: Optional[str] = None
    last_check: datetime = Field(default_factory=datetime.now)


class SystemState(BaseModel):
    mode: Literal["full", "partial", "emergency"]
    modules: list[ModuleState]
    resources: Dict = Field(default_factory=dict)