"""
Tools Module - Утилитный модуль для работы с файлами и текстом
"""
import os
import zipfile
from typing import Any, Dict, List, Optional, Tuple

from core.bundle_json_read import is_bundle_json_member, parse_zip_inner_spec, shape_bundle_json_payload
from core.models import Output

_MAX_ZIP_LIST = min(500, int(os.getenv("TOOLS_ZIP_MAX_LIST", "200")))
_MAX_ZIP_MEMBER_BYTES = min(2_000_000, int(os.getenv("TOOLS_ZIP_MAX_MEMBER_BYTES", "800000")))
_MAX_ZIP_TOTAL_MANIFEST = min(80_000_000, int(os.getenv("TOOLS_ZIP_MAX_TOTAL_BYTES", "50000000")))


def _slash_cmd_rest(payload: str) -> tuple[str, str]:
    p = (payload or "").strip()
    if not p.startswith("/"):
        return "", p
    sp = p.split(maxsplit=1)
    head = sp[0].lstrip("/").split("@")[0].lower()
    tail = sp[1].strip() if len(sp) > 1 else ""
    return head, tail


def _text_from_document_intake(context: Any) -> str:
    if not isinstance(context, dict):
        return ""
    doc = context.get("document_intake")
    if not isinstance(doc, dict) or not doc.get("ok"):
        return ""
    if doc.get("text_layer_empty"):
        return ""
    return str(doc.get("text") or "").strip()


def _safe_tools_filename(name: str) -> str:
    s = (name or "").strip().replace("\\", "/").split("/")[-1]
    if not s or s in {".", ".."}:
        return ""
    return s[:220]


def _file_context_local_zip(context: Dict[str, Any]) -> Tuple[str, str]:
    """Локальный путь к ZIP из вложения Telegram (если это zip)."""
    fc = context.get("file_context") if isinstance(context, dict) else None
    if not isinstance(fc, dict):
        return "", ""
    lp = str(fc.get("local_path") or "").strip()
    if not lp or not os.path.isfile(lp):
        return "", ""
    on = str(fc.get("original_name") or "upload.zip")
    mt = str(fc.get("mime_type") or "").lower()
    low = on.lower()
    if low.endswith(".zip") or mt in {"application/zip", "application/x-zip-compressed"} or "zip" in mt:
        return lp, on
    return "", ""


def _zip_normalize_member(name: str) -> str:
    n = (name or "").replace("\\", "/").strip()
    if ".." in n or n.startswith("/"):
        return ""
    return n


def _zip_find_inner_name(zf: zipfile.ZipFile, hint: str) -> str:
    hint = (hint or "").strip().replace("\\", "/")
    if not hint:
        return ""
    names = zf.namelist()
    if hint in names:
        return hint
    for n in names:
        if n.replace("\\", "/").rstrip("/").endswith(hint) or n.split("/")[-1] == hint:
            return n
    return ""


def _zip_default_members() -> List[str]:
    return [
        "bundle.json",
        "КАК_ЧИТАТЬ_ДИАГНОСТИКУ.txt",
        "КАК_ЧИТАТЬ_ДИАГНОСТИКУ.TXT",
    ]


def _diagnostic_inner_member_token(token: str) -> str:
    """
    Один аргумент без .zip — если это типичное имя файла из admin_diagnostic ZIP,
    вернуть канонический hint для чтения; иначе пустая строка.
    """
    t = (token or "").strip()
    if not t or t.lower().endswith(".zip"):
        return ""
    low = t.lower()
    if low == "bundle.json":
        return "bundle.json"
    for d in _zip_default_members():
        if low == d.lower():
            return d
    return ""


def _resolve_tools_zip_autopick(storage_path: str) -> str:
    """
    Выбрать ZIP в data/tools: сначала gemma_diagnostic_*.zip (новее),
    иначе gemma_bugreport_*.zip, иначе *diagnostic*.zip, иначе любой .zip (новее).
    """
    best_prio = 99
    best_mtime = 0.0
    best_path = ""
    try:
        for name in os.listdir(storage_path):
            if not name.lower().endswith(".zip"):
                continue
            p = os.path.join(storage_path, name)
            if not os.path.isfile(p):
                continue
            mtime = os.path.getmtime(p)
            nl = name.lower()
            if nl.startswith("gemma_diagnostic_"):
                prio = 0
            elif nl.startswith("gemma_bugreport_"):
                prio = 1
            elif "diagnostic" in nl:
                prio = 2
            else:
                prio = 3
            if prio < best_prio or (prio == best_prio and mtime > best_mtime):
                best_prio = prio
                best_mtime = mtime
                best_path = p
    except OSError:
        return ""
    return best_path


def _default_save_basename(context: Dict[str, Any]) -> str:
    fn = context.get("telegram_document_filename")
    if isinstance(fn, str) and fn.strip():
        base = fn.strip().rsplit(".", 1)[0] if "." in fn else fn.strip()
        base = _safe_tools_filename(base)
        if base:
            return f"{base}.txt"
    return "document.txt"


class ToolsModule:
    """Утилитный модуль для работы с файлами и текстом"""
    
    def __init__(self, config: Dict[str, Any] = None):
        """Инициализация модуля"""
        self.config = config or {}
        self.storage_path = self.config.get("storage_path", "./data/tools")
        os.makedirs(self.storage_path, exist_ok=True)

    def _zip_precheck(self, zf: zipfile.ZipFile) -> Optional[str]:
        infos = zf.infolist()
        if len(infos) > _MAX_ZIP_LIST * 3:
            return f"В архиве слишком много записей (>{_MAX_ZIP_LIST * 3})."
        total = 0
        for info in infos:
            name = info.filename.replace("\\", "/")
            if ".." in name or name.startswith("/"):
                return "Небезопасные пути внутри ZIP — отказ."
            total += int(info.file_size or 0)
            if total > _MAX_ZIP_TOTAL_MANIFEST:
                return "Суммарный размер файлов в ZIP по манифесту слишком большой."
        return None

    def _decode_zip_bytes(self, raw: bytes) -> str:
        for enc in ("utf-8", "utf-8-sig", "cp1251", "latin-1"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    def _zip_list_text(self, zip_path: str) -> str:
        with zipfile.ZipFile(zip_path, "r") as zf:
            err = self._zip_precheck(zf)
            if err:
                return err
            lines = [f"ZIP: {zip_path} ({len(zf.infolist())} записей)"]
            for info in zf.infolist()[:_MAX_ZIP_LIST]:
                if info.is_dir():
                    continue
                lines.append(f"• {info.filename} — {info.file_size} байт")
            if len(zf.infolist()) > _MAX_ZIP_LIST:
                lines.append(f"… показано {_MAX_ZIP_LIST} из {len(zf.infolist())}")
            return "\n".join(lines)

    def _zip_read_text_pair(self, zip_path: str, member_hint: str) -> Tuple[str, str]:
        """Возвращает (текст или сообщение об ошибке, имя entry в ZIP или пустая строка при ошибке)."""
        with zipfile.ZipFile(zip_path, "r") as zf:
            err = self._zip_precheck(zf)
            if err:
                return err, ""
            target = ""
            if member_hint:
                target = _zip_find_inner_name(zf, _zip_normalize_member(member_hint))
                if not target:
                    return f"Файл «{member_hint}» не найден в архиве. /zip_list — список имён.", ""
            else:
                for cand in _zip_default_members():
                    target = _zip_find_inner_name(zf, cand)
                    if target:
                        break
                if not target and zf.namelist():
                    for n in zf.namelist():
                        if not n.endswith("/"):
                            target = n
                            break
            if not target:
                return "Архив пуст или нет подходящего текстового файла.", ""
            info = zf.getinfo(target)
            if int(info.file_size or 0) > _MAX_ZIP_MEMBER_BYTES:
                return (
                    f"«{target}» слишком большой ({info.file_size} байт, лимит {_MAX_ZIP_MEMBER_BYTES}).",
                    "",
                )
            with zf.open(info) as f:
                raw = f.read(_MAX_ZIP_MEMBER_BYTES + 1)
            if len(raw) > _MAX_ZIP_MEMBER_BYTES:
                raw = raw[:_MAX_ZIP_MEMBER_BYTES]
            text = self._decode_zip_bytes(raw)
            head = f"=== {target} ({len(raw)} байт) ===\n"
            return head + text, target

    def _zip_read_text(self, zip_path: str, member_hint: str) -> str:
        body, _ = self._zip_read_text_pair(zip_path, member_hint)
        return body

    def _zip_pack(self, out_name: str, member_names: List[str]) -> Tuple[bool, str]:
        out_name = _safe_tools_filename(out_name)
        if not out_name.lower().endswith(".zip"):
            out_name += ".zip"
        out_path = os.path.join(self.storage_path, out_name)
        added = 0
        try:
            with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for m in member_names:
                    fn = _safe_tools_filename(m)
                    if not fn or fn == out_name:
                        continue
                    fp = os.path.join(self.storage_path, fn)
                    if not os.path.isfile(fp):
                        continue
                    zf.write(fp, arcname=fn)
                    added += 1
        except OSError as e:
            return False, str(e)
        if added == 0:
            try:
                if os.path.isfile(out_path):
                    os.remove(out_path)
            except OSError:
                pass
            return False, "Нет ни одного существующего файла из списка (каталог data/tools)."
        return True, f"Собран «{out_name}» ({added} файлов) в data/tools."

    async def execute(self, args: Dict[str, Any]) -> List[Output]:
        """Основной метод выполнения"""
        input_data = args.get("input", {})
        payload = input_data.get("payload", "")
        context = args.get("context") if isinstance(args.get("context"), dict) else {}
        body = _text_from_document_intake(context)
        cmd, rest = _slash_cmd_rest(str(payload or ""))

        if cmd == "read_file":
            if not rest:
                return [
                    Output(
                        type="text",
                        payload="Укажите имя файла: /read_file имя.txt (файлы лежат в каталоге data/tools на сервере).",
                        meta={"module": "tools"},
                    )
                ]
            filename = _safe_tools_filename(rest)
            if not filename:
                return [Output(type="text", payload="Некорректное имя файла.", meta={"module": "tools"})]
            content = self._read_file(filename)
            if content:
                return [
                    Output(
                        type="text",
                        payload=f"Содержимое файла {filename}:\n{content}",
                        meta={"module": "tools", "action": "read_file", "file": filename},
                    )
                ]
            return [
                Output(
                    type="text",
                    payload=f"Файл {filename} не найден в хранилище.",
                    meta={"module": "tools", "action": "read_file", "file": filename},
                )
            ]

        if cmd == "save_file":
            if not rest and body:
                out_name = _default_save_basename(context)
                self._save_file(out_name, body)
                return [
                    Output(
                        type="text",
                        payload=(
                            f"Сохранил извлечённый из вложения текст в «{out_name}» ({len(body)} симв.). "
                            f"Другое имя: /save_file мой_файл.txt"
                        ),
                        meta={"module": "tools", "action": "save_file", "file": out_name, "source": "attachment"},
                    )
                ]
            if not rest:
                return [
                    Output(
                        type="text",
                        payload=(
                            "Сохранение в каталог бота (data/tools):\n"
                            "• Отправьте PDF/DOCX **снова** (Telegram не передаёт старый файл в новом сообщении) и в подписи: "
                            "`/save_file kn-1011.txt` — текст возьмётся из вложения.\n"
                            "• В одном сообщении с файлом можно просто `/save_file` — имя файла будет как у вложения + .txt\n"
                            "• Без вложения, но с текстом вручную: `/save_file имя.txt ваш текст…`\n"
                            "• Для поиска по документу позже: `/add_book Название` в подписи к файлу, затем `/search_book …`\n"
                            "/read_file имя.txt — прочитать сохранённое."
                        ),
                        meta={"module": "tools"},
                    )
                ]
            parts = rest.split(" ", 1)
            if len(parts) == 1:
                fn = _safe_tools_filename(parts[0])
                if not fn:
                    return [Output(type="text", payload="Некорректное имя файла.", meta={"module": "tools"})]
                if not body:
                    return [
                        Output(
                            type="text",
                            payload=(
                                f"Нет текста из вложения для «{fn}». Пришлите документ с подписью "
                                f"`/save_file {fn}` или добавьте текст: `/save_file {fn} ваш текст…`"
                            ),
                            meta={"module": "tools"},
                        )
                    ]
                self._save_file(fn, body)
                return [
                    Output(
                        type="text",
                        payload=f"Файл «{fn}» сохранён из вложения ({len(body)} симв.).",
                        meta={"module": "tools", "action": "save_file", "file": fn, "source": "attachment"},
                    )
                ]
            filename, content = _safe_tools_filename(parts[0]), parts[1]
            if not filename:
                return [Output(type="text", payload="Некорректное имя файла.", meta={"module": "tools"})]
            self._save_file(filename, content)
            return [
                Output(
                    type="text",
                    payload=f"Файл «{filename}» сохранён.",
                    meta={"module": "tools", "action": "save_file", "file": filename},
                )
            ]

        if cmd == "zip_list":
            fc_path, fc_label = _file_context_local_zip(context)
            rest_s = rest.strip()
            zip_path = ""
            label = ""
            if fc_path:
                zip_path, label = fc_path, fc_label
            elif rest_s:
                fn = _safe_tools_filename(rest_s)
                if fn.lower().endswith(".zip"):
                    cand = os.path.join(self.storage_path, fn)
                    if os.path.isfile(cand):
                        zip_path, label = cand, fn
            if not zip_path:
                return [
                    Output(
                        type="text",
                        payload=(
                            "Список файлов в ZIP:\n"
                            "• Прикрепите архив и в подписи: /zip_list\n"
                            "• Или: /zip_list имя.zip — файл из каталога data/tools на сервере бота"
                        ),
                        meta={"module": "tools"},
                    )
                ]
            try:
                txt = self._zip_list_text(zip_path)
            except zipfile.BadZipFile:
                return [
                    Output(
                        type="text",
                        payload="Файл не является корректным ZIP.",
                        meta={"module": "tools"},
                    )
                ]
            except Exception as e:
                return [
                    Output(
                        type="text",
                        payload=f"Ошибка чтения ZIP: {e}",
                        meta={"module": "tools"},
                    )
                ]
            return [
                Output(
                    type="text",
                    payload=txt,
                    meta={"module": "tools", "action": "zip_list", "file": label},
                )
            ]

        if cmd == "zip_read":
            fc_path, _fc_l = _file_context_local_zip(context)
            inner = rest.strip()
            zip_read_autopick: bool = False
            if fc_path:
                zip_path = fc_path
                member_hint = inner
            else:
                parts = inner.split(None, 1)
                if not parts:
                    return [
                        Output(
                            type="text",
                            payload=(
                                "Текст из файла внутри ZIP:\n"
                                "• Прикрепите архив: /zip_read или /zip_read bundle.json\n"
                                "• bundle.json по умолчанию — сводка (экономия токенов). Полный JSON: "
                                "full=1 или mode=full.\n"
                                "• Секция: bundle.json section=performance · путь: path=diagnostic_snapshot.monitoring\n"
                                "• Частями: chunk=1/5 (к итоговому тексту).\n"
                                "• Или: /zip_read имя.zip файл_внутри [опции] — из data/tools\n"
                                "• Только: /zip_read bundle.json — новейший diagnostic/bugreport ZIP в data/tools\n"
                                "• Переменные: TOOLS_BUNDLE_JSON_DEFAULT_MODE, TOOLS_BUNDLE_JSON_SUMMARY_MAX_CHARS\n"
                                "Без имени внутри пробуются: bundle.json, КАК_ЧИТАТЬ_ДИАГНОСТИКУ.txt"
                            ),
                            meta={"module": "tools"},
                        )
                    ]
                arc = _safe_tools_filename(parts[0])
                member_hint = parts[1].strip() if len(parts) > 1 else ""
                if len(parts) == 1 and not arc.lower().endswith(".zip"):
                    inner_only = _diagnostic_inner_member_token(parts[0])
                    if inner_only:
                        auto_zip = _resolve_tools_zip_autopick(self.storage_path)
                        if not auto_zip:
                            return [
                                Output(
                                    type="text",
                                    payload=(
                                        "Нет ни одного .zip в data/tools. Варианты:\n"
                                        "• Снова выполните /admin_diagnostic (или /admin_bug) — при "
                                        "ADMIN_DIAGNOSTIC_COPY_TO_TOOLS=1 архив копируется в data/tools автоматически.\n"
                                        "• Прикрепите ZIP к сообщению и напишите: /zip_read bundle.json\n"
                                        "• Или сохраните файл в data/tools и: /zip_read имя_архива.zip bundle.json"
                                    ),
                                    meta={"module": "tools"},
                                )
                            ]
                        zip_path = auto_zip
                        member_hint = inner_only
                        zip_read_autopick = True
                    else:
                        return [
                            Output(
                                type="text",
                                payload=(
                                    "Укажите архив с расширением .zip в data/tools, например:\n"
                                    "/zip_read gemma_diagnostic_….zip bundle.json\n\n"
                                    "Если в каталоге уже лежит свежий gemma_diagnostic_*.zip — достаточно:\n"
                                    "/zip_read bundle.json"
                                ),
                                meta={"module": "tools"},
                            )
                        ]
                else:
                    if not arc.lower().endswith(".zip"):
                        return [
                            Output(
                                type="text",
                                payload="Укажите архив с расширением .zip (в data/tools).",
                                meta={"module": "tools"},
                            )
                        ]
                    cand = os.path.join(self.storage_path, arc)
                    if not os.path.isfile(cand):
                        return [
                            Output(
                                type="text",
                                payload=(
                                    f"Файл «{arc}» не найден в data/tools. "
                                    "Если только что брали архив из чата — выполните /admin_diagnostic ещё раз "
                                    "(копия появится в data/tools) или прикрепите ZIP к сообщению с /zip_read bundle.json."
                                ),
                                meta={"module": "tools"},
                            )
                        ]
                    zip_path = cand
            try:
                inner_clean, bundle_opts = parse_zip_inner_spec(member_hint)
                hint_for_zip = inner_clean if inner_clean else (member_hint.strip() or "")
                if not inner_clean and (bundle_opts.get("section") or bundle_opts.get("path")):
                    hint_for_zip = "bundle.json"
                body, arcname = self._zip_read_text_pair(zip_path, hint_for_zip)
                if not arcname:
                    return [
                        Output(
                            type="text",
                            payload=body,
                            meta={"module": "tools"},
                        )
                    ]
                if is_bundle_json_member(arcname):
                    body = shape_bundle_json_payload(
                        body,
                        bundle_opts,
                        member_label=arcname.replace("\\", "/").split("/")[-1],
                    )
                if zip_read_autopick:
                    body = f"Использован архив: {os.path.basename(zip_path)}\n\n{body}"
            except zipfile.BadZipFile:
                return [
                    Output(
                        type="text",
                        payload="Файл не является корректным ZIP.",
                        meta={"module": "tools"},
                    )
                ]
            except Exception as e:
                return [
                    Output(
                        type="text",
                        payload=f"Ошибка: {e}",
                        meta={"module": "tools"},
                    )
                ]
            return [
                Output(
                    type="text",
                    payload=body,
                    meta={"module": "tools", "action": "zip_read"},
                )
            ]

        if cmd == "zip_pack":
            parts = rest.split()
            if len(parts) < 2:
                return [
                    Output(
                        type="text",
                        payload=(
                            "Сборка ZIP из файлов в data/tools:\n"
                            "/zip_pack итог.zip файл1.txt файл2.json …\n"
                            "Все входные имена — только из каталога хранилища бота (без путей)."
                        ),
                        meta={"module": "tools"},
                    )
                ]
            out_raw = parts[0]
            members = [_safe_tools_filename(x) for x in parts[1:]]
            members = [x for x in members if x]
            ok, msg = self._zip_pack(out_raw, members)
            return [
                Output(
                    type="text",
                    payload=msg if ok else f"Не удалось: {msg}",
                    meta={"module": "tools", "action": "zip_pack", "ok": ok},
                )
            ]

        if cmd == "parse":
            if not rest:
                return [
                    Output(
                        type="text",
                        payload="Укажите текст: /parse строка или абзац",
                        meta={"module": "tools"},
                    )
                ]
            parsed = self._parse_text(rest)
            return [
                Output(
                    type="text",
                    payload=f"Результат парсинга:\n{chr(10).join(parsed)}",
                    meta={"module": "tools", "action": "parse_text"},
                )
            ]

        return [
            Output(
                type="text",
                payload=(
                    "Команды модуля tools:\n"
                    "/read_file <имя> — прочитать из data/tools\n"
                    "/zip_list — список файлов в ZIP (вложение или /zip_list имя.zip в data/tools)\n"
                    "/zip_read — прочитать текст из ZIP (диагностика: bundle.json и т.д.; см. справку команды)\n"
                    "/zip_pack итог.zip f1 f2 … — запаковать файлы из data/tools\n"
                    "/save_file — сохранить текст из вложения (см. справку)\n"
                    "/save_file <имя> — из вложения\n"
                    "/save_file <имя> <текст> — записать текст\n"
                    "/parse <текст> — разбить на непустые строки"
                ),
                meta={"module": "tools"},
            )
        ]
    
    def _read_file(self, filename: str) -> str:
        """Прочитать файл"""
        try:
            filepath = os.path.join(self.storage_path, filename)
            if os.path.exists(filepath):
                with open(filepath, 'r', encoding='utf-8') as f:
                    return f.read()
            return ""
        except Exception as e:
            print(f"Error reading file: {e}")
            return ""
    
    def _save_file(self, filename: str, content: str):
        """Сохранить файл"""
        try:
            filepath = os.path.join(self.storage_path, filename)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
        except Exception as e:
            print(f"Error saving file: {e}")
    
    def _parse_text(self, text: str) -> List[str]:
        """Парсинг текста"""
        # Простой парсинг - разбиение на строки
        lines = text.split('\n')
        # Фильтрация пустых строк
        return [line.strip() for line in lines if line.strip()]