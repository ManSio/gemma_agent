# Voice (STT / TTS)

Off by default. Enable only when models/binaries are installed.

## TTS (Piper)

```env
VOICE_ENABLED=true
VOICE_TTS_ENABLED=true
VOICE_TTS_BACKEND=piper
VOICE_TTS_MODEL_PATH=./models/piper/ru_RU-irina-medium.onnx
PIPER_BIN=/usr/local/bin/piper
VOICE_REPLY_ENABLED=true
```

Download model to `models/piper/` from [rhasspy/piper releases](https://github.com/rhasspy/piper/releases).

## STT (local)

```env
VOICE_STT_ENABLED=true
VOICE_STT_BACKEND=vosk
VOICE_STT_MODEL_PATH=./models/vosk/ru-small
VOICE_STT_LOCAL_ONLY=true
```

Requires `ffmpeg` for Telegram OGG.

## Cloud fallback

`VOICE_STT_FALLBACK_BACKEND=openrouter` sends audio to OpenRouter — see [security model](../security/security-model.md).

Implementation: `core/voice_module.py`
