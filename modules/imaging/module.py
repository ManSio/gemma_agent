"""Реестр плагина: PIL-утилиты подключаются из vision/skills, не дублируем тяжёлый init."""


class ImagingModule:
    """Маркер загрузки пакета imaging для PluginRegistry."""

    def __init__(self) -> None:
        pass
