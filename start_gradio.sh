#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

# Local secrets live in .env.local. This file is ignored by Git.
if [[ -f ".env.local" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env.local"
  set +a
fi

# Values exported before running this script take priority over these defaults.
export DASHSCOPE_API_KEY="${DASHSCOPE_API_KEY:-your-dashscope-api-key}"
export GRADIO_USERNAME="${GRADIO_USERNAME:-lilong}"
export GRADIO_PASSWORD="${GRADIO_PASSWORD:-lilong}"

HOST="${GRADIO_HOST:-0.0.0.0}"
PORT="${GRADIO_PORT:-7860}"

if [[ -z "$DASHSCOPE_API_KEY" || "$DASHSCOPE_API_KEY" == "your-dashscope-api-key" ]]; then
  echo "Please set DASHSCOPE_API_KEY before starting."
  echo "Example: export DASHSCOPE_API_KEY=\"sk-xxxxxxxxxxxxxxxxxxxxxxxx\""
  echo "Or edit .env.local and add: DASHSCOPE_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx"
  exit 1
fi

if [[ -z "$GRADIO_PASSWORD" ]]; then
  echo "Please set GRADIO_PASSWORD before starting."
  echo "Example: export GRADIO_PASSWORD=\"lilong\""
  exit 1
fi

exec python main.py --host "$HOST" --port "$PORT" --share
