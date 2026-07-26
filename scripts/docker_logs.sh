#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/docker.sh"

require_docker

cd "${PROJECT_ROOT}"

# Follow logs for the app service (or a service passed as the first arg).
compose logs -f --tail="${LOG_TAIL:-200}" "${1:-app}"
