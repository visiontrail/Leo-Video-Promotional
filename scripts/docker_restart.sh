#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/docker.sh"

require_docker
ensure_env_file

cd "${PROJECT_ROOT}"

echo "[docker-restart] Rebuilding and restarting the Video-Promotional stack..."
echo "[docker-restart] Runtime data in ./outputs, ./uploads, and ./data is preserved."
compose down --remove-orphans
compose up -d --build

echo
compose ps
print_endpoints
