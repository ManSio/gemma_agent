#!/usr/bin/env bash
# Нативный SearXNG (systemd) — как на HOST_LAN. Без Docker.
#
#   sudo bash scripts/searxng_install_native.sh
#   sudo STOP_DOCKER_SEARX=1 bash scripts/searxng_install_native.sh   # миграция с gemma-searxng
#
# Пути (как deploy-host): /usr/local/searxng-src, /usr/local/searxng/venv, :8080
set -euo pipefail

BOT_DIR="${GEMMA_BOT_DIR:-/opt/gemma_agent}"
SRC_DIR="${SEARXNG_SRC_DIR:-/usr/local/searxng-src}"
VENV_DIR="${SEARXNG_VENV_DIR:-/usr/local/searxng/venv}"
SETTINGS_DIR="/etc/searxng"
SETTINGS_FILE="${SEARXNG_SETTINGS_PATH:-$SETTINGS_DIR/settings.yml}"
SEARX_USER="${SEARXNG_USER:-searxng}"
STOP_DOCKER_SEARX="${STOP_DOCKER_SEARX:-0}"

log() { echo "[searxng-native] $*"; }
die() { echo "[searxng-native] ERROR: $*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "run as root"

export DEBIAN_FRONTEND=noninteractive

stop_docker_searx() {
  if ! command -v docker >/dev/null 2>&1; then
    return 0
  fi
  local compose="$BOT_DIR/infra/searxng/docker-compose.yml"
  if [[ -f "$compose" ]]; then
    log "Останавливаем Docker SearXNG (gemma-searxng)..."
    docker compose -f "$compose" down 2>/dev/null || docker stop gemma-searxng 2>/dev/null || true
    docker rm gemma-searxng 2>/dev/null || true
  fi
  if [[ "${DISABLE_DOCKER_DAEMON:-0}" == "1" ]]; then
    if docker ps -q 2>/dev/null | grep -q .; then
      log "Docker: другие контейнеры ещё работают — dockerd не отключаем"
    else
      log "Отключаем docker.service (контейнеров нет)..."
      systemctl disable --now docker 2>/dev/null || true
    fi
  fi
}

install_packages() {
  log "Пакеты для сборки SearXNG..."
  apt-get update -qq
  apt-get install -y -qq \
    python3-dev python3-babel python3-venv python-is-python3 \
    git build-essential libxslt-dev zlib1g-dev libffi-dev libssl-dev curl
}

ensure_user() {
  if ! id "$SEARX_USER" &>/dev/null; then
    log "Пользователь $SEARX_USER..."
    useradd --shell /bin/bash --system \
      --home-dir "/usr/local/searxng" \
      --comment "SearXNG metasearch" "$SEARX_USER"
  fi
  mkdir -p "/usr/local/searxng" "$SRC_DIR" "$SETTINGS_DIR"
  chown -R "$SEARX_USER:$SEARX_USER" "/usr/local/searxng" "$SRC_DIR"
}

clone_or_update_src() {
  if [[ -d "$SRC_DIR/.git" ]]; then
    log "git pull $SRC_DIR"
    sudo -u "$SEARX_USER" git -C "$SRC_DIR" pull --ff-only 2>/dev/null || true
  else
    log "git clone -> $SRC_DIR"
    sudo -u "$SEARX_USER" git clone --depth 1 https://github.com/searxng/searxng.git "$SRC_DIR"
  fi
}

install_venv() {
  if [[ ! -x "$VENV_DIR/bin/python3" ]]; then
    log "venv $VENV_DIR"
    sudo -u "$SEARX_USER" python3 -m venv "$VENV_DIR"
  fi
  log "pip install searxng (editable)..."
  sudo -u "$SEARX_USER" "$VENV_DIR/bin/pip" install -q -U pip setuptools wheel pyyaml msgspec typing-extensions pybind11
  sudo -u "$SEARX_USER" bash -c "
    cd '$SRC_DIR' && '$VENV_DIR/bin/pip' install -q --use-pep517 --no-build-isolation -e .
  "
}

install_settings() {
  local src_settings="$BOT_DIR/infra/searxng/settings.yml"
  [[ -f "$src_settings" ]] || die "нет $src_settings"
  if [[ -f "$SETTINGS_FILE" ]]; then
    cp -a "$SETTINGS_FILE" "${SETTINGS_FILE}.bak-gemma-$(date -u +%Y%m%dT%H%M%SZ)"
  fi
  cp "$src_settings" "$SETTINGS_FILE"
  if grep -q 'change-me-on-prod' "$SETTINGS_FILE"; then
    local secret
    secret="$(openssl rand -hex 32)"
    sed -i "s/change-me-on-prod-use-openssl-rand-hex-32/$secret/" "$SETTINGS_FILE"
  fi
  chown root:"$SEARX_USER" "$SETTINGS_FILE"
  chmod 640 "$SETTINGS_FILE"
  log "settings: $SETTINGS_FILE"
}

install_systemd() {
  local unit_src="$BOT_DIR/infra/systemd/searxng.service"
  [[ -f "$unit_src" ]] || die "нет $unit_src"
  # Пути как на deploy-host
  sed \
    -e "s|/usr/local/searxng-src|$SRC_DIR|g" \
    -e "s|/usr/local/searxng/venv|$VENV_DIR|g" \
    "$unit_src" > /etc/systemd/system/searxng.service
  if ! grep -q 'SEARXNG_SETTINGS_PATH' /etc/systemd/system/searxng.service; then
    sed -i "/Environment=PYTHONPATH/a Environment=SEARXNG_SETTINGS_PATH=$SETTINGS_FILE" \
      /etc/systemd/system/searxng.service
  fi
  systemctl daemon-reload
  systemctl enable searxng
  systemctl restart searxng
}

verify_http() {
  sleep 2
  local code json_code
  code="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/ || echo 000)"
  json_code="$(curl -s -o /dev/null -w '%{http_code}' 'http://127.0.0.1:8080/search?q=test&format=json' || echo 000)"
  log "HTTP / → $code"
  log "HTTP /search?format=json → $json_code"
  [[ "$code" == "200" || "$code" == "302" ]] || die "SearXNG не отвечает на :8080"
  [[ "$json_code" == "200" ]] || die "SearXNG json format недоступен (боту нужен format=json)"
}

[[ "$STOP_DOCKER_SEARX" == "1" ]] && stop_docker_searx

install_packages
ensure_user
clone_or_update_src
install_venv
install_settings
install_systemd

if [[ -x "$BOT_DIR/scripts/vps_tune_searxng_max.sh" ]]; then
  bash "$BOT_DIR/scripts/vps_tune_searxng_max.sh" || log "warn: vps_tune_searxng_max не применился"
fi

verify_http
log "OK: SearXNG native systemd на :8080"
