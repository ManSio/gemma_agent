# Голос (STT / TTS)

По умолчанию выключено.

## TTS (Piper)

```env
VOICE_TTS_ENABLED=true
VOICE_TTS_MODEL_PATH=./models/piper/ru_RU-irina-medium.onnx
PIPER_BIN=/usr/local/bin/piper
```

Модель в `models/piper/`.

## STT (Vosk локально)

```env
VOICE_STT_ENABLED=true
VOICE_STT_BACKEND=vosk
VOICE_STT_MODEL_PATH=./models/vosk/ru-small
```

Нужен ffmpeg для OGG из Telegram.

## Облако

Fallback на OpenRouter — см. [безопасность](../security/security-model.ru.md).

Код: `core/voice_module.py`
