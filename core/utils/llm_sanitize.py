def sanitize_llm_value(value):
    """
    Универсальная защита инструментов от мусора LLM.
    Гарантирует, что на выходе всегда безопасная строка.
    """
    if value is None:
        return ""

    if isinstance(value, list):
        flat = []
        for x in value:
            if isinstance(x, list):
                flat.extend(map(str, x))
            elif x is None:
                continue
            else:
                flat.append(str(x))
        return "\n".join(flat).strip()

    if isinstance(value, dict):
        import json
        try:
            return json.dumps(value, ensure_ascii=False, indent=2).strip()
        except Exception:
            return str(value).strip()

    if isinstance(value, (int, float, bool)):
        return str(value)

    try:
        return str(value).strip()
    except Exception:
        return ""
