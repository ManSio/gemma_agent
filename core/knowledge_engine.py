from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional


class KnowledgeEngine:
    """Freshness/version/trust-aware knowledge helper."""

    def __init__(self, *, ttl_hours_default: int = 72) -> None:
        self.sources: List[Dict[str, Any]] = []
        self.ttl_hours_default = max(1, int(ttl_hours_default))

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _parse_ts(self, ts: str) -> Optional[datetime]:
        try:
            return datetime.fromisoformat(ts)
        except Exception:
            return None

    def _is_fresh(self, row: Dict[str, Any]) -> bool:
        ts = self._parse_ts(str(row.get("ts", "")))
        if ts is None:
            return False
        ttl_hours = row.get("ttl_hours")
        try:
            ttl = int(ttl_hours) if ttl_hours is not None else self.ttl_hours_default
        except Exception:
            ttl = self.ttl_hours_default
        return ts >= (datetime.now(timezone.utc) - timedelta(hours=max(1, ttl)))

    def ingest(
        self,
        source: str,
        content: str,
        *,
        version: str = "1",
        tags: List[str] | None = None,
        trust: float = 0.7,
        ttl_hours: Optional[int] = None,
    ) -> None:
        self.sources.append(
            {
                "source": source,
                "content": content,
                "version": version,
                "tags": tags or [],
                "trust": max(0.0, min(1.0, float(trust))),
                "ttl_hours": max(1, int(ttl_hours)) if ttl_hours is not None else self.ttl_hours_default,
                "ts": self._now_iso(),
            }
        )

    def reset(self) -> None:
        self.sources = []

    def ingest_context_sources(self, *, context: Dict[str, Any]) -> int:
        """
        Build normalized knowledge rows from runtime context.
        Rebuilds per-turn pool (no long-term mutation from this helper).
        """
        self.reset()
        ctx = context if isinstance(context, dict) else {}
        added = 0

        user_facts = ctx.get("user_facts")
        if isinstance(user_facts, dict):
            for key, value in user_facts.items():
                if value in (None, "", [], {}):
                    continue
                self.ingest(
                    source=f"facts:{key}",
                    content=f"{key}={value}",
                    version="facts-v1",
                    tags=[str(key), "profile", "facts"],
                    trust=0.92,
                    ttl_hours=24 * 30,
                )
                added += 1

        mem0_facts = ctx.get("mem0_facts")
        if isinstance(mem0_facts, list):
            for idx, item in enumerate(mem0_facts[:20]):
                if not item:
                    continue
                text = str(item)
                self.ingest(
                    source=f"mem0:{idx}",
                    content=text,
                    version="mem0-v1",
                    tags=["memory", "profile"],
                    trust=0.8,
                    ttl_hours=24 * 14,
                )
                added += 1

        topic_tracking = ctx.get("topic_tracking")
        if isinstance(topic_tracking, dict):
            for key, value in topic_tracking.items():
                if value in (None, "", [], {}):
                    continue
                self.ingest(
                    source=f"topic:{key}",
                    content=f"{key}:{value}",
                    version="topic-v1",
                    tags=[str(key), "topic", "conversation"],
                    trust=0.72,
                    ttl_hours=24 * 3,
                )
                added += 1

        recent = ctx.get("recent_dialogue") or ctx.get("recent_messages")
        if isinstance(recent, list):
            for idx, row in enumerate(recent[-8:]):
                if not row:
                    continue
                self.ingest(
                    source=f"dialogue:{idx}",
                    content=str(row),
                    version="dialogue-v1",
                    tags=["conversation", "recent_dialogue"],
                    trust=0.58,
                    ttl_hours=24,
                )
                added += 1
        return added

    def select_for_intent(self, intent: str) -> Dict[str, Any]:
        # Prefer fresh + trusted rows, then intent tag match, then recency.
        rows = list(self.sources)
        if not rows:
            return {"selected": [], "policy": "none", "confidence": 0.0}

        fresh_rows = [r for r in rows if self._is_fresh(r)]
        pool = fresh_rows or rows
        tagged = [r for r in pool if intent in (r.get("tags") or [])]
        ranked = tagged or pool
        ranked.sort(
            key=lambda r: (
                float(r.get("trust", 0.0)),
                str(r.get("ts", "")),
            ),
            reverse=True,
        )
        selected = ranked[:3]
        confidence = 0.0
        if selected:
            confidence = round(sum(float(r.get("trust", 0.0)) for r in selected) / len(selected), 3)
        return {
            "selected": selected,
            "policy": "fresh_trusted_tagged",
            "confidence": confidence,
            "fresh_pool": bool(fresh_rows),
        }
