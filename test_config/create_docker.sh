#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
docker run --init --rm --name sony-home-assistant \
  --network host \
  -v "$project_dir/test_config:/config" \
  -v "$project_dir/custom_components:/config/custom_components:ro" \
  ghcr.io/home-assistant/home-assistant:2026.10.2
