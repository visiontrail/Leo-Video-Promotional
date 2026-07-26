#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/docker.sh"

require_docker

cd "${PROJECT_ROOT}"

echo "[docker-down] Stopping the Video-Promotional stack..."
echo "[docker-down] Runtime data in ./outputs, ./uploads, and ./data is preserved."
compose down --remove-orphans
echo
echo "[docker-down] Persisted data is stored beside the source code and is never"
echo "              removed by Docker Compose."
