"""Unified Lesson data model for the SelfLearningEngine."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid


@dataclass
class Lesson:
    """A unit of knowledge gained from a past error or success."""

    id: str
    content: str
    source: str  # "reflexion", "manual", "consolidation"
    source_context: Dict[str, Any]
    created_at: str
    last_accessed_at: str
    access_count: int = 0
    strength: float = 1.0
    effectiveness_score: float = 0.5
    tags: List[str] = field(default_factory=list)
    category: str = "general"
    status: str = "active"  # "active", "deprecated", "consolidated", "retired"
    retires_at: Optional[str] = None

    @staticmethod
    def new(
        *,
        content: str,
        source: str = "reflexion",
        source_context: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
        category: str = "general",
    ) -> Lesson:
        now = datetime.now(timezone.utc).isoformat()
        return Lesson(
            id=f"lesson_{uuid.uuid4().hex[:12]}",
            content=content,
            source=source,
            source_context=source_context or {},
            created_at=now,
            last_accessed_at=now,
            access_count=0,
            strength=1.0,
            effectiveness_score=0.5,
            tags=tags or [],
            category=category,
            status="active",
            retires_at=None,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "source": self.source,
            "source_context": self.source_context,
            "created_at": self.created_at,
            "last_accessed_at": self.last_accessed_at,
            "access_count": self.access_count,
            "strength": self.strength,
            "effectiveness_score": self.effectiveness_score,
            "tags": self.tags,
            "category": self.category,
            "status": self.status,
            "retires_at": self.retires_at,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> Lesson:
        return Lesson(
            id=d.get("id", ""),
            content=d.get("content", ""),
            source=d.get("source", "reflexion"),
            source_context=d.get("source_context", {}),
            created_at=d.get("created_at", ""),
            last_accessed_at=d.get("last_accessed_at", ""),
            access_count=d.get("access_count", 0),
            strength=float(d.get("strength", 1.0)),
            effectiveness_score=float(d.get("effectiveness_score", 0.5)),
            tags=d.get("tags", []),
            category=d.get("category", "general"),
            status=d.get("status", "active"),
            retires_at=d.get("retires_at"),
        )
