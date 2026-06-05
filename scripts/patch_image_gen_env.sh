#!/usr/bin/env bash
# OPS: выставить IMAGE_GEN_* для Nano Banana 2 на сервере (идемпотентно).
#   cd /opt/gemma_agent && bash scripts/patch_image_gen_env.sh
set -euo pipefail

ROOT="${GEMMA_BOT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[ERROR] нет $ENV_FILE" >&2
  exit 1
fi

KEYS=(
  IMAGE_GEN_MODEL
  IMAGE_GEN_MODEL_FALLBACK
  IMAGE_GEN_MODALITIES
  IMAGE_GEN_TIMEOUT_SEC
  IMAGE_GEN_REFERENCE_ENABLED
  IMAGE_GEN_REFERENCE_MAX_BYTES
  IMAGE_GEN_REFERENCE_MAX_COUNT
  IMAGE_GEN_IMAGE_CONFIG
  IMAGE_GEN_PENDING_WAIT_MS
  IMAGE_PHOTO_ONLY_ACK
  IMAGE_PENDING_MAX_PHOTOS
)

VALUES=(
  google/gemini-3.1-flash-image-preview
  google/gemini-2.5-flash-image
  image,text
  120
  true
  4194304
  3
  true
  2500
  true
  3
)

tmp="$(mktemp)"
grep -v -E '^(IMAGE_GEN_MODEL|IMAGE_GEN_MODEL_FALLBACK|IMAGE_GEN_MODALITIES|IMAGE_GEN_TIMEOUT_SEC|IMAGE_GEN_REFERENCE_ENABLED|IMAGE_GEN_REFERENCE_MAX_BYTES|IMAGE_GEN_REFERENCE_MAX_COUNT|IMAGE_GEN_IMAGE_CONFIG|IMAGE_GEN_PENDING_WAIT_MS|IMAGE_PHOTO_ONLY_ACK|IMAGE_PENDING_MAX_PHOTOS)=' "$ENV_FILE" >"$tmp" || true
for i in "${!KEYS[@]}"; do
  echo "${KEYS[$i]}=${VALUES[$i]}" >>"$tmp"
done
mv "$tmp" "$ENV_FILE"
chmod 600 "$ENV_FILE" 2>/dev/null || true

echo "[OK] patch_image_gen_env: ${#KEYS[@]} ключей"
grep -E '^IMAGE_GEN_(MODEL|MODEL_FALLBACK|REFERENCE_ENABLED|TIMEOUT_SEC)=' "$ENV_FILE"
