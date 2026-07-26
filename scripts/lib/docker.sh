#!/usr/bin/env bash
# Shared helpers for the docker_*.sh scripts. Source this; don't run directly.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE_FILE="${PROJECT_ROOT}/docker-compose.yml"
ENV_TEMPLATE="${PROJECT_ROOT}/.env.example"
ENV_FILE="${PROJECT_ROOT}/.env"

if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  echo "[docker] Docker Compose is not installed. Install Docker Compose v2 or docker-compose." >&2
  exit 1
fi

compose() {
  "${COMPOSE[@]}" -f "${COMPOSE_FILE}" "$@"
}

require_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "[docker] Docker is not installed." >&2
    exit 1
  fi
  if ! docker info >/dev/null 2>&1; then
    echo "[docker] Docker daemon is not reachable. Start Docker first." >&2
    exit 1
  fi
}

ensure_env_file() {
  if [[ ! -f "${ENV_FILE}" ]]; then
    if [[ ! -f "${ENV_TEMPLATE}" ]]; then
      echo "[docker] Missing env template: ${ENV_TEMPLATE}" >&2
      exit 1
    fi
    cp "${ENV_TEMPLATE}" "${ENV_FILE}"
    echo "[docker] Created .env from .env.example — fill in AI_API_KEY before running."
  fi
}

env_value() {
  local key="$1" default="$2" value="${!1:-}"
  if [[ -z "${value}" && -f "${ENV_FILE}" ]]; then
    value="$(
      awk -F= -v key="${key}" '
        $0 !~ /^[[:space:]]*#/ && $1 == key { sub(/^[^=]*=/, ""); print; exit }
      ' "${ENV_FILE}"
    )"
    value="${value%\"}"; value="${value#\"}"
    value="${value%\'}"; value="${value#\'}"
  fi
  [[ -n "${value}" ]] && printf '%s' "${value}" || printf '%s' "${default}"
}

print_endpoints() {
  local app_port
  app_port="$(env_value APP_PORT 8100)"
  echo
  echo "Endpoints:"
  echo "  App (UI + API): http://localhost:${app_port}"
  echo "  Health:         http://localhost:${app_port}/api/health"
  echo "  API docs:       http://localhost:${app_port}/docs"
}
