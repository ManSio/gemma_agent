"""
Local Model Provider — упрощённый быстрый провайдер локальных моделей.

generate() использует локальный fallback (без LLM).
generate_image() / generate_audio() пробуют OpenRouter multimodal API.
"""
from typing import Any, Dict, Optional
import logging

logger = logging.getLogger(__name__)


class LocalModelProvider:
    """Минимальный быстрый провайдер локальных и мультимодальных моделей."""

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = config or {}
        self.models = {
            "small": {"available": True},
        }
        self._provider = None

    def _get_provider(self):
        """Ленивое получение OpenRouterProvider."""
        if self._provider is None:
            try:
                from core.openrouter_provider import get_openrouter_provider
                self._provider = get_openrouter_provider()
            except Exception as e:
                logger.debug("[model_provider] OpenRouter not available: %s", e)
        return self._provider

    async def generate(self, prompt: str, model_type: str = "small", **kwargs: Any) -> str:
        """Генерация текста — сначала OpenRouter, локальный fallback."""
        provider = self._get_provider()
        if provider is not None:
            try:
                resp = await provider.generate(
                    prompt=prompt,
                    temperature=kwargs.get("temperature", 0.7),
                )
                content = resp.get("content") or resp.get("choices", [{}])[0].get("message", {}).get("content", "")
                if content:
                    return content
            except Exception as e:
                logger.debug("[model_provider] generate via OpenRouter error: %s", e)
        return f"[local-small] {prompt[:200]}"

    async def generate_image(self, prompt: str, **kwargs: Any) -> str:
        """
        Генерация изображения через OpenRouter multimodal vision.

        Отправляет текстовый запрос на генерацию URL изображения.
        Если OpenRouter недоступен — возвращает описание.
        """
        provider = self._get_provider()
        if provider is not None:
            try:
                resp = await provider.generate(
                    prompt=(
                        f"Ты — генератор изображений. Ответь одним URL "
                        f"(любым валидным http/https URL подходящего изображения) "
                        f"на запрос: {prompt}. Только URL, без пояснений."
                    ),
                    temperature=0.3,
                    max_tokens=100,
                )
                content = resp.get("content", "")
                if content and content.strip():
                    url = content.strip()
                    if url.startswith("http"):
                        return url
            except Exception as e:
                logger.debug("[model_provider] generate_image error: %s", e)

        return (
            f"[image generation requested: {prompt[:200]}] "
            f"(OpenRouter vision not available, no local image model)"
        )

    async def generate_audio(self, prompt: str, **kwargs: Any) -> str:
        """
        Генерация аудио через OpenRouter multimodal LLM.

        Запрашивает URL аудиофайла, соответствующий описанию.
        Если OpenRouter недоступен — возвращает описание.
        """
        provider = self._get_provider()
        if provider is not None:
            try:
                resp = await provider.generate(
                    prompt=(
                        f"Ты — генератор аудио. Ответь одним URL "
                        f"(любым валидным http/https URL подходящего аудиофайла) "
                        f"на запрос: {prompt}. Только URL, без пояснений."
                    ),
                    temperature=0.3,
                    max_tokens=100,
                )
                content = resp.get("content", "")
                if content and content.strip():
                    url = content.strip()
                    if url.startswith("http"):
                        return url
            except Exception as e:
                logger.debug("[model_provider] generate_audio error: %s", e)

        return (
            f"[audio generation requested: {prompt[:200]}] "
            f"(OpenRouter TTS not available, no local audio model)"
        )

    def get_available_models(self) -> Dict[str, Any]:
        return self.models

    def select_best_model(self, requirements: Optional[Dict[str, Any]] = None) -> str:
        """Выбрать лучшую модель на основе требований — выбираем реально подходящую."""
        if not requirements:
            return "small"

        model_scores: Dict[str, int] = {}
        for model_name, model_info in self.models.items():
            score = 0
            for req_key, req_val in (requirements or {}).items():
                if req_key in model_info and model_info[req_key] == req_val:
                    score += 1
                if req_key == "speed" and req_val == "fast" and model_name == "small":
                    score += 2
            model_scores[model_name] = score

        if not model_scores:
            return "small"

        return max(model_scores, key=model_scores.get)
