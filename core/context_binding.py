"""
Context binder: resolves pronominal references ("там", "в нём", "этот", etc.)
and named objects ("указ 95") to the last mentioned object (file, document, corpus item, image).
Allows the agent to understand back-references without LLM re-analysis.
"""

from __future__ import annotations

import logging
import re
from collections import deque
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CONTEXT_BINDING_VERSION = "3.0.0"

SUBJECT_PRONOUNS: List[str] = [
    "я",
    "мне",
    "меня",
    "мой",
    "моя",
    "мои",
    "моё",
    "мое",
]

SUBJECT_TRIGGERS: List[str] = [
    "я", "меня", "мне", "мой", "моя", "мои", "моё", "мое",
    "сколько мне", "что я", "про меня", "мой профиль",
]

PRONOUNS: List[str] = [
    "внутри него",
    "внутри них",
    "внутри",
    "там",
    "в нём",
    "в них",
    "этот",
    "тот",
    "они",
    "его",
    "их",
]

TYPE_PRONOUNS: Dict[str, List[str]] = {
    "document": [
        "этот документ",
        "тот документ",
        "этот указ",
        "тот указ",
        "этот акт",
        "тот акт",
        "этот текст",
        "тот текст",
        "прочитай документ",
        "прочти документ",
        "открой документ",
        "покажи документ",
    ],
    "image": [
        "эта картинка",
        "та картинка",
        "это изображение",
        "то изображение",
        "это фото",
        "то фото",
        "этот снимок",
        "тот снимок",
    ],
    "file": [
        "этот файл",
        "тот файл",
        "этот архив",
        "тот архив",
    ],
}

DOCUMENT_NAME_RE: re.Pattern = re.compile(
    r"(?:указ|постановлени[ея]|декрет|закон|кодекс|распоряжени[ея])"
    r"\s*(?:№|#|n|номер|no\.?)?\s*(\d{1,4}(?:[-\u2013\u2014]\d{1,2})?)",
    re.IGNORECASE,
)


@dataclass
class BoundObject:
    type: str
    id: str
    path: str
    title: str
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "id": self.id,
            "path": self.path,
            "title": self.title,
            "metadata": self.metadata,
        }


class ContextBinder:
    _MAX_HISTORY = 5

    def __init__(self):
        self._object_history: deque[BoundObject] = deque(maxlen=self._MAX_HISTORY)

    @property
    def last_object(self) -> Optional[BoundObject]:
        return self._object_history[-1] if self._object_history else None

    def update(
        self,
        *,
        type: str,
        id: str = "",
        path: str = "",
        title: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        obj = BoundObject(
            type=type,
            id=str(id or ""),
            path=str(path or ""),
            title=str(title or ""),
            metadata=metadata if isinstance(metadata, dict) else {},
        )
        self._object_history.append(obj)
        logger.debug("context_binder updated: type=%s title=%s id=%s (history=%d)",
                     type, title, id, len(self._object_history))

    def _find_last_by_type(self, obj_type: str) -> Optional[BoundObject]:
        for obj in reversed(self._object_history):
            if obj.type == obj_type:
                return obj
        return None

    def resolve_object_by_name(self, text: str) -> Optional[BoundObject]:
        """
        Resolve a named document reference in text (e.g. "указ 95", "указ №95").
        Returns a BoundObject with type="document" if a match is found.
        """
        if not text:
            return None
        m = DOCUMENT_NAME_RE.search(text)
        if not m:
            return None
        doc_num = m.group(1)
        doc_type = m.group(0).split()[0].lower() if m.group(0) else ""
        title = f"{m.group(0).strip()}"
        logger.debug("context_binder resolve_object_by_name: %s → document", title)
        return BoundObject(
            type="document",
            id=f"named:{doc_type}:{doc_num}",
            path="",
            title=title,
            metadata={"doc_type": doc_type, "doc_number": doc_num, "source": "named_reference"},
        )

    def resolve_pronoun(self, text: str) -> Optional[BoundObject]:
        if not text or not self._object_history:
            return None
        low = (text or "").strip().lower()
        if not low:
            return None

        for obj_type, type_pronouns in TYPE_PRONOUNS.items():
            for phrase in type_pronouns:
                if phrase in low:
                    last_of_type = self._find_last_by_type(obj_type)
                    if last_of_type is not None:
                        logger.debug(
                            "context_binder resolved type-pronoun=%r -> %s %s",
                            phrase, last_of_type.type, last_of_type.title,
                        )
                        return last_of_type

        for pronoun in PRONOUNS:
            if pronoun in low:
                logger.debug(
                    "context_binder resolved pronoun=%r -> %s %s",
                    pronoun,
                    self.last_object.type,
                    self.last_object.title,
                )
                return self.last_object

        return None

    def resolve_subject(self, text: str) -> Optional[BoundObject]:
        """Resolve subject triggers (я, мне, меня, мой, ..., сколько мне, что я, про меня, мой профиль) to a user BoundObject."""
        if not text:
            return None
        low = text.lower()
        if any(p in low for p in SUBJECT_TRIGGERS):
            return BoundObject(
                type="subject",
                id="user",
                path="",
                title="Пользователь",
                metadata={},
            )
        return None

    def clear(self) -> None:
        self._object_history.clear()


TOOL_HINT_MAP: Dict[str, str] = {
    "document": "DocumentIntake.process_document",
    "corpus_item": "DocumentCorpus.unified_search",
    "file": "FileIntake.process_file",
    "image": "VisionLayer.describe",
}


def bound_object_tool_hint(bound: BoundObject) -> Dict[str, Any]:
    hint = TOOL_HINT_MAP.get(bound.type, "")
    return {
        "type": bound.type,
        "id": bound.id,
        "path": bound.path,
        "title": bound.title,
        "tool_hint": hint,
        "metadata": bound.metadata,
    }


def can_persist_user_fact(source: str, confirmed: bool) -> bool:
    """Memory-safety guard 2.0: only persist user facts that were explicitly stated
    by the user AND explicitly confirmed.
    Block auto-inferred facts (country_inference, guessed, inferred)."""
    if not confirmed:
        return False
    if source != "user_input":
        return False
    return True
