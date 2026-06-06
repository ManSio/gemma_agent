from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import shlex
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any, Dict, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _resolve_voice_path(raw: str) -> str:
    """Относительные пути — от корня репозитория, не от cwd процесса (systemd/docker часто ломают ./models)."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    if os.getenv("VOICE_PATHS_RELATIVE_TO_CWD", "").strip().lower() in {"1", "true", "yes", "on"}:
        return str(Path(raw).resolve())
    p = Path(raw)
    if p.is_absolute():
        return str(p.resolve())
    return str((_REPO_ROOT / raw).resolve())

import aiohttp

from core.error_analysis import record_error_event
from core.runtime_telegram_settings import effective_bool

logger = logging.getLogger(__name__)

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"


def _guess_audio_format(path: str) -> str:
    lower = path.lower()
    for ext, fmt in (
        (".wav", "wav"),
        (".ogg", "ogg"),
        (".opus", "ogg"),
        (".mp3", "mp3"),
        (".m4a", "m4a"),
        (".flac", "flac"),
        (".mp4", "mp4"),
        (".webm", "webm"),
    ):
        if lower.endswith(ext):
            return fmt
    return "ogg"


def _content_from_chat_completion(data: Dict[str, Any]) -> str:
    try:
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        c = msg.get("content")
        if isinstance(c, str):
            return c.strip()
        if isinstance(c, list):
            texts: list[str] = []
            for block in c:
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(str(block.get("text") or ""))
            return "".join(texts).strip()
    except Exception:
        return ""
    return ""


class VoiceModule:
    def __init__(self) -> None:
        self.enabled = os.getenv("VOICE_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
        self.stt_enabled = os.getenv("VOICE_STT_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
        self.tts_enabled = os.getenv("VOICE_TTS_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
        self.reply_enabled = os.getenv("VOICE_REPLY_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
        self.stt_local_only = os.getenv("VOICE_STT_LOCAL_ONLY", "false").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        _has_openai = bool((os.getenv("VOICE_STT_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip())
        _has_openrouter = bool(os.getenv("OPENROUTER_API_KEY", "").strip())
        explicit = (os.getenv("VOICE_STT_BACKEND") or "").strip()
        if explicit:
            default_backend = explicit.lower()
        elif self.stt_local_only:
            # Не переключать STT в облако только из‑за ключей чата (OpenRouter/OpenAI)
            default_backend = "vosk"
        elif _has_openai:
            default_backend = "openai"
        elif _has_openrouter:
            default_backend = "openrouter"
        else:
            default_backend = "vosk"
        self.stt_backend = default_backend
        self.tts_backend = os.getenv("VOICE_TTS_BACKEND", "piper")
        self.stt_model_path = _resolve_voice_path(os.getenv("VOICE_STT_MODEL_PATH", ""))
        self.tts_model_path = _resolve_voice_path(os.getenv("VOICE_TTS_MODEL_PATH", ""))
        raw_fb = (os.getenv("VOICE_STT_FALLBACK_BACKEND") or "").strip().lower()
        self._stt_fallback_auto_openrouter = False
        _cloud_fb = {"openai", "whisper", "whisper-1", "api", "openrouter", "or"}
        if self.stt_local_only and raw_fb in _cloud_fb:
            self._stt_fallback = ""
        elif raw_fb:
            self._stt_fallback = raw_fb
        elif (
            effective_bool("VOICE_STT_AUTO_OPENROUTER_FALLBACK", default=True)
            and self.stt_backend == "vosk"
            and _has_openrouter
            and not self.stt_local_only
        ):
            self._stt_fallback = "openrouter"
            self._stt_fallback_auto_openrouter = True
        else:
            self._stt_fallback = ""

    def stt_status(self) -> Dict[str, Any]:
        return {
            "voice_enabled": self.enabled,
            "stt_enabled": self.stt_enabled,
            "stt_local_only": self.stt_local_only,
            "stt_backend": self.stt_backend,
            "stt_fallback_backend": self._stt_fallback or None,
            "stt_fallback_auto_openrouter": bool(getattr(self, "_stt_fallback_auto_openrouter", False)),
            "stt_model_path_set": bool(self.stt_model_path),
            "stt_model_dir_exists": bool(self.stt_model_path and os.path.isdir(self.stt_model_path)),
            "stt_model_path_resolved": self.stt_model_path or None,
            "openai_key_configured": bool(
                (os.getenv("VOICE_STT_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
            ),
            "openrouter_key_configured": bool(os.getenv("OPENROUTER_API_KEY", "").strip()),
            "openrouter_stt_model": (os.getenv("VOICE_OPENROUTER_STT_MODEL") or "").strip() or None,
            "vosk_ffmpeg": os.getenv("VOICE_VOSK_FFMPEG", "true").strip().lower()
            in {"1", "true", "yes", "on"},
            "openrouter_stt_model_effective": (
                (os.getenv("VOICE_OPENROUTER_STT_MODEL") or "").strip()
                or (os.getenv("OPENROUTER_MODEL_FREE") or "").strip()
                or "google/gemini-2.0-flash-001"
            ),
        }

    def stt_empty_operator_hint(self) -> str:
        """Короткая подсказка в чат, если STT вернул пустую строку (без HTML)."""
        s = self.stt_status()
        b = str(s.get("stt_backend") or "?")
        parts: list[str] = [f"Сейчас STT backend: {b}."]

        if s.get("stt_local_only"):
            parts.append(
                "VOICE_STT_LOCAL_ONLY=true — облако для STT не используется; нужны vosk+VOICE_STT_MODEL_PATH+ffmpeg "
                "или whisper.cpp, либо снимите LOCAL_ONLY и задайте VOICE_STT_BACKEND=openai|openrouter."
            )

        if b == "vosk":
            if not s.get("stt_model_path_set"):
                parts.append("Для vosk укажите VOICE_STT_MODEL_PATH (каталог модели vosk).")
            elif not s.get("stt_model_dir_exists"):
                parts.append(
                    f"Каталог модели не найден: {s.get('stt_model_path_resolved') or '?'}. "
                    "Пути ./models/... считаются от корня репозитория (где лежит core/). "
                    "Или задайте абсолютный путь / VOICE_PATHS_RELATIVE_TO_CWD=true."
                )
            elif not s.get("vosk_ffmpeg"):
                parts.append("Для OGG из Telegram обычно нужен ffmpeg (VOICE_VOSK_FFMPEG=true, VOICE_FFMPEG_BIN).")
            else:
                parts.append(
                    "Vosk вернул пусто: тишина/шум, очень короткое сообщение или сбой ffmpeg — "
                    "/admin_logs 50 voice: «vosk: пустая транскрипция», «ffmpeg vosk convert failed». "
                )
                if s.get("stt_fallback_backend") == "openrouter" or s.get("stt_fallback_auto_openrouter"):
                    parts.append(
                        "OpenRouter fallback уже включён (авто при vosk+ключ или VOICE_STT_FALLBACK_BACKEND). "
                        "Если текст всё равно пуст — runtime_errors: «stt openrouter chat error»; "
                        "задайте VOICE_OPENROUTER_STT_MODEL с поддержкой input_audio (напр. google/gemini-2.0-flash-001)."
                    )
                elif s.get("openrouter_key_configured"):
                    parts.append(
                        "С OPENROUTER_API_KEY можно включить подстраховку: VOICE_STT_FALLBACK_BACKEND=openrouter "
                        "или оставьте по умолчанию (авто openrouter при vosk)."
                    )
                else:
                    parts.append("Подстраховка: OPENROUTER_API_KEY + VOICE_STT_FALLBACK_BACKEND=openrouter.")

        if b in {"openai", "whisper", "whisper-1", "api"}:
            if not s.get("openai_key_configured"):
                parts.append("Нет ключа для Whisper API: OPENAI_API_KEY или VOICE_STT_API_KEY.")
            else:
                parts.append("При наличии ключа смотрите runtime_errors: события «stt API error» / «stt openai request failed».")

        if b in {"openrouter", "or"}:
            if not s.get("openrouter_key_configured"):
                parts.append("Нет OPENROUTER_API_KEY для STT через OpenRouter.")
            elif not (os.getenv("VOICE_OPENROUTER_STT_MODEL") or "").strip():
                parts.append(
                    f"VOICE_OPENROUTER_STT_MODEL не задан — используется «{s.get('openrouter_stt_model_effective')}». "
                    "Задайте модель с поддержкой аудио (например google/gemini-2.0-flash-001)."
                )
            else:
                parts.append(
                    "OpenRouter STT вернул пусто: модель могла не отдать текст — смотрите «stt openrouter chat error» в runtime_errors."
                )

        fb = s.get("stt_fallback_backend")
        if fb:
            auto = " (авто)" if s.get("stt_fallback_auto_openrouter") else ""
            parts.append(f"Fallback STT: {fb}{auto}.")

        parts.append(
            "Сводка настроек голоса: команда /admin_operator в Telegram. "
            "Док: docs/OPERATIONS_AND_ADMIN.md (раздел «Голос (STT)»)."
        )
        return " ".join(parts)

    async def _stt_openai_compatible(self, audio_path: str) -> str:
        url = (os.getenv("VOICE_STT_API_URL") or "https://api.openai.com/v1/audio/transcriptions").strip()
        key = (os.getenv("VOICE_STT_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
        model = (os.getenv("VOICE_STT_MODEL") or "whisper-1").strip()
        if not key:
            record_error_event("voice", "stt openai: no API key", extra={"hint": "VOICE_STT_API_KEY or OPENAI_API_KEY"})
            return ""
        try:
            with open(audio_path, "rb") as f:
                audio_bytes = f.read()
        except OSError as e:
            record_error_event("voice", "stt openai: read file failed", exc=e)
            return ""

        form = aiohttp.FormData()
        form.add_field(
            "file",
            audio_bytes,
            filename=os.path.basename(audio_path) or "audio.ogg",
            content_type="application/octet-stream",
        )
        form.add_field("model", model)
        lang = os.getenv("VOICE_STT_LANGUAGE", "").strip()
        if lang:
            form.add_field("language", lang)

        headers: Dict[str, str] = {"Authorization": f"Bearer {key}"}
        if "openrouter.ai" in url:
            headers["HTTP-Referer"] = os.getenv("OPENROUTER_HTTP_REFERER", "https://github.com/ManSio/gemma_agent")
            headers["X-Title"] = os.getenv("OPENROUTER_X_TITLE", "Gemma Agent")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=form, headers=headers, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                    body = await resp.text()
                    if resp.status != 200:
                        record_error_event(
                            "voice",
                            "stt API error",
                            extra={"status": resp.status, "body": body[:500], "url": url},
                        )
                        return ""
                    try:
                        payload = json.loads(body)
                    except json.JSONDecodeError:
                        return ""
                    return str(payload.get("text") or "").strip()
        except Exception as e:
            record_error_event("voice", "stt openai request failed", exc=e, extra={"url": url})
            return ""

    async def _stt_openrouter_multimodal(self, audio_path: str) -> str:
        key = (os.getenv("OPENROUTER_API_KEY") or os.getenv("VOICE_STT_API_KEY") or "").strip()
        model = (
            os.getenv("VOICE_OPENROUTER_STT_MODEL")
            or os.getenv("OPENROUTER_MODEL_FREE")
            or "google/gemini-2.0-flash-001"
        ).strip()
        if not key:
            record_error_event("voice", "stt openrouter: no OPENROUTER_API_KEY", exc=None)
            return ""
        try:
            with open(audio_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
        except OSError as e:
            record_error_event("voice", "stt openrouter: read file failed", exc=e)
            return ""

        fmt = (os.getenv("VOICE_OPENROUTER_AUDIO_FORMAT") or "").strip() or _guess_audio_format(audio_path)
        prompt = (
            os.getenv("VOICE_OPENROUTER_STT_PROMPT")
            or "Transcribe the speech. Reply with only the transcript, same language as the speaker. No translation."
        )

        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "input_audio", "input_audio": {"data": b64, "format": fmt}},
                    ],
                }
            ],
        }

        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": os.getenv("OPENROUTER_HTTP_REFERER", "https://github.com/ManSio/gemma_agent"),
            "X-Title": os.getenv("OPENROUTER_X_TITLE", "Gemma Agent"),
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    OPENROUTER_CHAT_URL,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as resp:
                    body = await resp.text()
                    if resp.status != 200:
                        record_error_event(
                            "voice",
                            "stt openrouter chat error",
                            extra={"status": resp.status, "body": body[:800]},
                        )
                        return ""
                    try:
                        data = json.loads(body)
                    except json.JSONDecodeError:
                        return ""
                    return _content_from_chat_completion(data)
        except Exception as e:
            record_error_event("voice", "stt openrouter request failed", exc=e)
            return ""

    def _ffmpeg_to_wav16k_mono(self, src: str) -> str:
        if os.getenv("VOICE_VOSK_FFMPEG", "true").strip().lower() not in {"1", "true", "yes", "on"}:
            return ""
        ffmpeg = os.getenv("VOICE_FFMPEG_BIN", "ffmpeg")
        fd, out = tempfile.mkstemp(suffix=".wav", prefix="gemma_vosk_")
        os.close(fd)
        extra = shlex.split((os.getenv("VOICE_FFMPEG_EXTRA_ARGS") or "").strip())
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-y",
            "-i",
            src,
            *extra,
            "-ar",
            "16000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            "-f",
            "wav",
            out,
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=120)
            if r.returncode != 0 or not os.path.isfile(out):
                err = (r.stderr or b"").decode("utf-8", errors="replace").strip()
                tail = err[-1200:] if err else ""
                record_error_event(
                    "voice",
                    "ffmpeg vosk convert failed",
                    extra={
                        "returncode": r.returncode,
                        "stderr_tail": tail,
                        "src_basename": os.path.basename(src),
                    },
                )
                try:
                    os.remove(out)
                except OSError:
                    pass
                return ""
            return out
        except Exception as e:
            record_error_event("voice", "ffmpeg convert for vosk failed", exc=e)
            try:
                os.remove(out)
            except OSError:
                pass
            return ""

    def _stt_vosk_sync(self, audio_path: str) -> str:
        try:
            from vosk import Model, KaldiRecognizer  # type: ignore
        except Exception as e:
            record_error_event("voice", "vosk import failed", exc=e)
            return ""
        if not self.stt_model_path:
            return ""
        if not os.path.isdir(self.stt_model_path):
            record_error_event(
                "voice",
                "vosk: VOICE_STT_MODEL_PATH не каталог или путь неверный",
                extra={"path": self.stt_model_path, "repo_root": str(_REPO_ROOT)},
            )
            return ""

        try:
            in_size = os.path.getsize(audio_path)
        except OSError:
            in_size = 0

        path = audio_path
        converted = ""
        try:
            wave.open(audio_path, "rb").close()
        except Exception:
            converted = self._ffmpeg_to_wav16k_mono(audio_path)
            if not converted:
                record_error_event(
                    "voice",
                    "vosk: нужен WAV или установите ffmpeg (VOICE_FFMPEG_BIN) для конвертации OGG из Telegram",
                    exc=None,
                    extra={"input_basename": os.path.basename(audio_path), "input_bytes": in_size},
                )
                return ""
            path = converted

        wf = None
        try:
            wf = wave.open(path, "rb")
            sample_rate = wf.getframerate()
            nframes = wf.getnframes()
            if nframes == 0:
                record_error_event(
                    "voice",
                    "vosk: WAV без сэмплов после конвертации",
                    extra={
                        "input_basename": os.path.basename(audio_path),
                        "input_bytes": in_size,
                        "wav_basename": os.path.basename(path),
                    },
                )
                return ""
            model = Model(self.stt_model_path)
            rec = KaldiRecognizer(model, sample_rate)
            while True:
                data = wf.readframes(4000)
                if len(data) == 0:
                    break
                rec.AcceptWaveform(data)
            out = rec.FinalResult()
            payload = json.loads(out)
            txt = (payload.get("text") or "").strip()
            if not txt:
                dur = round(nframes / float(sample_rate or 1), 3)
                record_error_event(
                    "voice",
                    "vosk: пустая транскрипция",
                    extra={
                        "model_path": self.stt_model_path,
                        "input_basename": os.path.basename(audio_path),
                        "input_bytes": in_size,
                        "wav_basename": os.path.basename(path),
                        "sample_rate": sample_rate,
                        "nframes": nframes,
                        "duration_sec_approx": dur,
                        "hint": "Если duration > 1 с — попробуйте другую модель vosk или VOICE_STT_FALLBACK_BACKEND=openrouter; "
                        "если < 0.3 с — слишком короткое сообщение.",
                    },
                )
            return txt
        except Exception as e:
            record_error_event("voice", "vosk recognition failed", exc=e)
            return ""
        finally:
            if wf:
                try:
                    wf.close()
                except Exception as e:
                    logger.debug('%s optional failed: %s', 'voice_module', e, exc_info=True)
            if converted and converted != audio_path:
                try:
                    os.remove(converted)
                except OSError:
                    pass

    async def _stt_whisper_cpp(self, audio_path: str) -> str:
        if not self.stt_model_path:
            return ""
        bin_path = os.getenv("WHISPER_CPP_BIN", "whisper-cli")
        fd, out_base = tempfile.mkstemp(prefix="whisper_", suffix="")
        os.close(fd)
        try:
            os.remove(out_base)
        except OSError:
            pass
        cmd = [bin_path, "-m", self.stt_model_path, "-f", audio_path, "-of", out_base]

        def _run() -> None:
            subprocess.run(cmd, check=False, capture_output=True)

        await asyncio.to_thread(_run)
        out_txt = out_base + ".txt"
        if os.path.isfile(out_txt):
            with open(out_txt, "r", encoding="utf-8", errors="ignore") as f:
                return f.read().strip()
        return ""

    async def _stt_dispatch(self, audio_path: str, backend: str) -> str:
        b = (backend or "").strip().lower()
        try:
            if b in {"openai", "whisper", "whisper-1", "api"}:
                return await self._stt_openai_compatible(audio_path)
            if b in {"openrouter", "or"}:
                return await self._stt_openrouter_multimodal(audio_path)
            if b == "vosk":
                return await asyncio.to_thread(self._stt_vosk_sync, audio_path)
            if b in {"whispercpp", "whisper.cpp"}:
                return await self._stt_whisper_cpp(audio_path)
        except Exception as e:
            record_error_event("voice", "stt dispatch failed", exc=e, extra={"backend": b})
        return ""

    async def stt(self, audio_path: str) -> str:
        if not (self.enabled and self.stt_enabled):
            return ""
        primary = self.stt_backend
        text = await self._stt_dispatch(audio_path, primary)
        if text.strip():
            return text.strip()
        fb = self._stt_fallback
        if fb and fb != primary:
            logger.info(
                "STT primary %s вернул пусто — fallback %s",
                primary,
                fb,
                extra={"gemma_event": "voice_stt_fallback", "primary": primary, "fallback": fb},
            )
            text = await self._stt_dispatch(audio_path, fb)
        return (text or "").strip()

    async def tts(self, text: str) -> Optional[str]:
        from core.utils.llm_sanitize import sanitize_llm_value
        text = sanitize_llm_value(text)
        if not (self.enabled and self.tts_enabled):
            return None
        try:
            if self.tts_backend == "piper":
                bin_path = os.getenv("PIPER_BIN", "piper")
                fd, out_wav = tempfile.mkstemp(prefix="tts_", suffix=".wav")
                os.close(fd)
                if not self.tts_model_path:
                    return None
                cmd = [bin_path, "-m", self.tts_model_path, "-f", out_wav]
                subprocess.run(cmd, input=text.encode("utf-8"), check=False, capture_output=True)
                return out_wav if os.path.isfile(out_wav) else None
            if self.tts_backend == "silero":
                import torch  # type: ignore

                _ = torch
                return None
        except Exception as e:
            record_error_event("voice", "tts failed", exc=e, extra={"backend": self.tts_backend})
        return None
