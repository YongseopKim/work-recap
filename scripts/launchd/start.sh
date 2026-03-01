#!/bin/bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$PROJECT_DIR"

# Ensure log directory exists
mkdir -p .log

# Activate virtual environment
source .venv/bin/activate

# Start server
exec uvicorn workrecap.api.app:app --host 0.0.0.0 --port 9090
