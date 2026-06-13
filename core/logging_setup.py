"""
Единый вывод логов для контейнера и локали.

- LOG_FORMAT=console — колонки, опционально цвет уровня (TTY или LOG_USE_COLOR=1)
- LOG_FORMAT=json — одна строка JSON на событие (удобно grep/jq по docker logs)
- GEMMA_REPORT_TIMEZONE — IANA-зона для asctime/ts в логах и админ-отчётов (напр. Europe/Minsk); иначе UTC

В extra нельзя использовать ключ "module" (зарезервирован в LogRecord). Для маршрута плагина — "gemma_module".
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from collections import deque
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from threading import Lock
from typing import Any, Deque, Dict, Optional

from core.log_paths import resolved_process_log_file_path
from core.path_redaction import redact_public_path
from core.report_timezone import get_report_tz, json_iso_timestamp, log_line_timestamp
from core.request_context import get_request_id


RESET = "\033[0m"
LEVEL_COLORS = {
    "DEBUG": "\033[36m",     # cyan
    "INFO": "\033[32m",      # green
    "WARNING": "\033[33m",   # yellow
    "ERROR": "\033[31m",     # red
    "CRITICAL": "\033[35m",  # magenta
}


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _use_color() -> bool:
    if _truthy("LOG_USE_COLOR", False):
        return True
    if os.getenv("LOG_USE_COLOR", "").strip().lower() in {"0", "false", "no", "off"}:
        return False
    return sys.stdout.isatty()


class RequestIdFilter(logging.Filter):
    """Attach correlation id to every log record when bound."""

    def filter(self, record: logging.LogRecord) -> bool:
        rid = get_request_id()
        if rid and not getattr(record, "gemma_request_id", None):
            record.gemma_request_id = rid
        return True


class UtcFormatter(logging.Formatter):
    """Время в зоне GEMMA_REPORT_TIMEZONE / LOG_TIMEZONE (иначе UTC, как раньше)."""

    def formatTime(self, record: logging.LogRecord, datefmt: Optional[str] = None) -> str:
        if datefmt:
            dt = datetime.fromtimestamp(record.created, tz=get_report_tz())
            return dt.strftime(datefmt)
        return log_line_timestamp(record.created)


class ConsoleFormatter(UtcFormatter):
    """Читаемые колонки: время │ уровень │ логгер │ сообщение."""

    def __init__(self, *, use_color: bool) -> None:
        super().__init__(
            fmt="%(asctime)s │ %(levelname)-8s │ %(name)s │ %(message)s",
        )
        self._use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        rid = getattr(record, "gemma_request_id", None) or get_request_id()
        if rid:
            parts = line.split(" │ ", 3)
            if len(parts) >= 4:
                parts[3] = f"[{rid}] {parts[3]}"
                line = " │ ".join(parts)
        if not self._use_color:
            return line
        lvl = record.levelname
        c = LEVEL_COLORS.get(lvl, "")
        if not c:
            return line
        # подсветить только слово уровня (второе поле между │)
        parts = line.split(" │ ", 3)
        if len(parts) >= 2:
            parts[1] = f"{c}{parts[1]}{RESET}"
            line = " │ ".join(parts)
        return line


class CompactFormatter(UtcFormatter):
    """Короткий формат для файлов: ts|lvl|lg|msg (+ несколько ключевых extra)."""

    def __init__(self) -> None:
        super().__init__()

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record)
        lvl = str(record.levelname or "")[:4]
        lg = str(record.name or "")
        msg = record.getMessage()
        extras: list[str] = []
        for k in ("gemma_event", "trace_id", "gemma_module", "reason"):
            v = getattr(record, k, None)
            if v is not None and str(v).strip():
                extras.append(f"{k}={v}")
        tail = f" | {' '.join(extras)}" if extras else ""
        return f"{ts} | {lvl:4s} | {lg} | {msg}{tail}"


# Стандартные поля LogRecord — в JSON только если нужны; остальное из extra
_SKIP_JSON_KEYS = frozenset(
    {
        "name",
        "msg",
        "args",
        "created",
        "msecs",
        "relativeCreated",
        "levelno",
        "levelname",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "process",
        "processName",
        "thread",
        "threadName",
        "message",
        "asctime",
        "taskName",
    }
)

class JsonFormatter(UtcFormatter):
    """Строка JSON: ts, level, logger, msg; из record — поля вне _SKIP_JSON_KEYS (в т.ч. gemma_* из extra)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": json_iso_timestamp(record.created),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact_public_path(record.getMessage()),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        for k, v in record.__dict__.items():
            if k in _SKIP_JSON_KEYS or k.startswith("_"):
                continue
            if k in payload:
                continue
            if v is None:
                continue
            try:
                json.dumps(v, default=str)
            except TypeError:
                v = str(v)
            payload[k] = _redact_obj(v)
        return json.dumps(payload, ensure_ascii=False, default=str)


def _redact_obj(v: Any) -> Any:
    if isinstance(v, str):
        return redact_public_path(v)
    if isinstance(v, list):
        return [_redact_obj(x) for x in v]
    if isinstance(v, tuple):
        return tuple(_redact_obj(x) for x in v)
    if isinstance(v, dict):
        return {k: _redact_obj(x) for k, x in v.items()}
    return v


class PathRedactionFilter(logging.Filter):
    """
    Санитизирует пути в сообщениях и extra-полях до форматтеров/хендлеров.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if record.args:
                rendered = record.getMessage()
                record.msg = redact_public_path(rendered)
                record.args = ()
            else:
                record.msg = redact_public_path(record.msg)
        except Exception as e:
            logger.debug('%s optional failed: %s', 'logging_setup', e, exc_info=True)
        for k, v in list(record.__dict__.items()):
            if k.startswith("_") or k in _SKIP_JSON_KEYS:
                continue
            try:
                record.__dict__[k] = _redact_obj(v)
            except Exception as e:
                logger.debug('%s optional failed: %s', 'logging_setup', e, exc_info=True)
        return True


_FILE_LEGEND = (
    "# Gemma compact log legend\n"
    "# ts=timestamp, lvl=level, lg=logger, msg=message\n"
    "# extra keys: gemma_event=event, trace_id=trace, gemma_module=planned module, reason=decision reason\n"
)


class SizeAndTimeRotatingFileHandler(TimedRotatingFileHandler):
    """Rotate by time (hours) and by maxBytes threshold."""

    def __init__(
        self,
        filename: str,
        *,
        hours: int,
        max_bytes: int,
        backup_count: int,
        encoding: str = "utf-8",
    ) -> None:
        self.max_bytes = max(0, int(max_bytes))
        self._legend = _FILE_LEGEND
        super().__init__(
            filename=filename,
            when="H",
            interval=max(1, int(hours)),
            backupCount=max(1, int(backup_count)),
            encoding=encoding,
            utc=False,
        )
        self._ensure_legend()

    def _ensure_legend(self) -> None:
        try:
            if not self.baseFilename:
                return
            p = Path(self.baseFilename)
            if not p.exists() or p.stat().st_size == 0:
                with p.open("a", encoding=self.encoding or "utf-8") as f:
                    f.write(self._legend)
        except OSError:
            pass

    def shouldRollover(self, record: logging.LogRecord) -> int:  # type: ignore[override]
        if super().shouldRollover(record):
            return 1
        if self.max_bytes <= 0:
            return 0
        if self.stream is None:
            self.stream = self._open()
        try:
            msg = f"{self.format(record)}\n"
            self.stream.seek(0, os.SEEK_END)
            if self.stream.tell() + len(msg.encode(self.encoding or "utf-8", errors="ignore")) >= self.max_bytes:
                return 1
        except Exception:
            return 0
        return 0

    def doRollover(self) -> None:  # type: ignore[override]
        super().doRollover()
        self._ensure_legend()


class CriticalContextBuffer(logging.Handler):
    """In-memory tail of recent formatted log lines."""

    def __init__(self, max_lines: int = 120) -> None:
        super().__init__(level=logging.DEBUG)
        self._buf: Deque[str] = deque(maxlen=max(20, int(max_lines)))
        self._lock = Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
        except Exception:
            line = f"{record.levelname} {record.name}: {record.getMessage()}"
        with self._lock:
            self._buf.append(line)

    def snapshot(self, tail: int = 30) -> list[str]:
        with self._lock:
            rows = list(self._buf)
        return rows[-max(1, int(tail)) :]


class TransientTelegramErrorFilter(logging.Filter):
    """502/503 от Telegram — не писать в gemma_critical.log как катастрофу."""

    _TRANSIENT = (
        "bad gateway",
        "gateway timeout",
        "service unavailable",
        "failed to fetch updates",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno < logging.ERROR:
            return True
        msg = (record.getMessage() or "").lower()
        name = (record.name or "").lower()
        if "aiogram" in name and any(t in msg for t in self._TRANSIENT):
            record.levelno = logging.WARNING
            record.levelname = "WARNING"
        return True


class CriticalWithContextHandler(logging.Handler):
    """Writes ERROR/CRITICAL records to separate file with recent context preface."""

    def __init__(self, filename: str, context_buffer: CriticalContextBuffer, *, context_tail: int = 30) -> None:
        super().__init__(level=logging.ERROR)
        self._path = Path(filename)
        self._buf = context_buffer
        self._context_tail = max(5, int(context_tail))
        self._lock = Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            body = self.format(record)
        except Exception:
            body = f"{record.levelname} {record.name}: {record.getMessage()}"
        ts = json_iso_timestamp(record.created)
        ctx = self._buf.snapshot(self._context_tail)
        block = [
            f"\n=== CRITICAL_EVENT ts={ts} level={record.levelname} logger={record.name} ===",
            "Context before error:",
            *ctx,
            "---",
            body,
            "=== END_CRITICAL_EVENT ===\n",
        ]
        text = "\n".join(block)
        with self._lock:
            try:
                with self._path.open("a", encoding="utf-8") as f:
                    f.write(text)
            except OSError:
                pass


def setup_logging() -> None:
    """
    Инициализация root-логгера. Вызывать один раз после load_dotenv().
    LOG_LEVEL — DEBUG/INFO/WARNING/ERROR
    LOG_FORMAT — console | json | plain (plain = без цветов, как старый basicConfig)
    GEMMA_VERBOSE_CORE — принудительно DEBUG на root + тихие httpx/aiohttp (отладка ядра).
    GEMMA_CORE_LOG_FULL — то же + по умолчанию LATENCY_TRACE_LOG=all и GEMMA_LLM_AUDIT_LOG
    (удобно для docker logs: поставьте ещё LOG_FORMAT=json).

    Файл логов дублируется по умолчанию в data/logs/gemma_bot.log (см. BEHAVIOR_DATA_DIR).
    Только stdout: GEMMA_LOG_FILE_OFF=1. Свой путь: GEMMA_LOG_FILE=...
    """
    from core.env_flags import gemma_core_log_full

    if gemma_core_log_full():
        os.environ.setdefault("LATENCY_TRACE_LOG", "all")
        os.environ.setdefault("GEMMA_LLM_AUDIT_LOG", "true")

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    fmt = os.getenv("LOG_FORMAT", "console").strip().lower()
    root = logging.getLogger()
    # Явно снимаем все handlers (clear() эквивалентен, но list() стабильнее при кастомных подклассах).
    for _h in list(root.handlers):
        root.removeHandler(_h)
    root.setLevel(level)
    root.addFilter(PathRedactionFilter())
    root.addFilter(TransientTelegramErrorFilter())
    root.addFilter(RequestIdFilter())

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(level)

    # Подробные логи ядра (core.*): поднимаем уровень до DEBUG, библиотеки оставляем тихими
    if _truthy("GEMMA_VERBOSE_CORE") or gemma_core_log_full():
        root.setLevel(logging.DEBUG)
        stream_handler.setLevel(logging.DEBUG)

    if fmt == "json":
        stream_handler.setFormatter(JsonFormatter())
    elif fmt == "plain":
        stream_handler.setFormatter(
            UtcFormatter(fmt="%(asctime)s │ %(levelname)s │ %(name)s │ %(message)s")
        )
    else:
        stream_handler.setFormatter(ConsoleFormatter(use_color=_use_color()))

    root.addHandler(stream_handler)

    compact_file = _truthy("GEMMA_LOG_COMPACT", True)
    file_formatter: logging.Formatter = CompactFormatter() if compact_file else stream_handler.formatter  # type: ignore[assignment]

    # Файл логов на диске (тот же путь, что в core.log_paths — для диагностики и tail в bundle).
    log_path = resolved_process_log_file_path()
    if log_path:
        try:
            p = Path(log_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            try:
                mb = max(1, min(1024, int((os.getenv("GEMMA_LOG_FILE_MAX_MB") or "32").strip())))
            except ValueError:
                mb = 32
            try:
                _bk = int((os.getenv("GEMMA_LOG_FILE_BACKUPS") or "20").strip())
            except ValueError:
                _bk = 20
            try:
                _hours = int((os.getenv("GEMMA_LOG_ROTATE_HOURS") or "6").strip())
            except ValueError:
                _hours = 6
            fh = SizeAndTimeRotatingFileHandler(
                str(p),
                hours=max(1, min(24, _hours)),
                max_bytes=mb * 1024 * 1024,
                backup_count=max(2, min(128, _bk)),
                encoding="utf-8",
            )
            fh.setLevel(stream_handler.level)
            fh.setFormatter(file_formatter)
            root.addHandler(fh)
        except OSError as e:
            sys.stderr.write(f"[logging] GEMMA_LOG_FILE={log_path!r}: {e}\n")

    # Критический лог с контекстом перед ERROR/CRITICAL.
    if _truthy("GEMMA_CRITICAL_LOG_ENABLED", True):
        cpath = (os.getenv("GEMMA_CRITICAL_LOG_FILE") or "").strip()
        if not cpath:
            base = (os.getenv("BEHAVIOR_DATA_DIR") or "").strip() or os.path.join(os.getcwd(), "data")
            cpath = str(Path(base) / "logs" / "gemma_critical.log")
        try:
            context_buf = CriticalContextBuffer(
                max_lines=max(40, min(400, int((os.getenv("GEMMA_CRITICAL_CONTEXT_BUFFER_LINES") or "140").strip())))
            )
            context_buf.setFormatter(file_formatter)
            context_buf.setLevel(logging.DEBUG)
            root.addHandler(context_buf)

            ctail = max(5, min(120, int((os.getenv("GEMMA_CRITICAL_CONTEXT_TAIL_LINES") or "30").strip())))
            crit = CriticalWithContextHandler(cpath, context_buf, context_tail=ctail)
            crit.setFormatter(file_formatter)
            crit.setLevel(logging.ERROR)
            root.addHandler(crit)
        except Exception as e:
            sys.stderr.write(f"[logging] critical log setup failed: {e}\n")

    # шум сторонних библиотек
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("aiogram").setLevel(logging.INFO)
