#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <start_year> [workers]"
    echo "  start_year: GitHub 가입 연도 (e.g. 2015)"
    echo "  workers:    병렬 워커 수 (default: 3)"
    exit 1
fi

START_YEAR=$1
WORKERS=${2:-3}
CURRENT_YEAR=$(date +%Y)

# --- Precondition checks ---

if (( START_YEAR > CURRENT_YEAR )); then
    echo "Error: start_year($START_YEAR) > current_year($CURRENT_YEAR)"
    exit 1
fi

# Activate .venv if not already active
VENV_DIR="${SCRIPT_DIR}/.venv"
if [[ -z "${VIRTUAL_ENV:-}" ]]; then
    if [[ ! -d "$VENV_DIR" ]]; then
        echo "Error: .venv not found at ${VENV_DIR}"
        echo "  Run: python -m venv .venv && pip install -e '.[dev]'"
        exit 1
    fi
    echo "Activating ${VENV_DIR} ..."
    # shellcheck disable=SC1091
    source "${VENV_DIR}/bin/activate"
fi

if ! command -v workrecap &>/dev/null; then
    echo "Error: 'workrecap' command not found"
    echo "  Run: pip install -e '.[dev]' in the project .venv"
    exit 1
fi

if [[ ! -f "${SCRIPT_DIR}/.env" ]]; then
    echo "Error: .env not found (GHES credentials required)"
    exit 1
fi

if [[ ! -f "${SCRIPT_DIR}/.provider/config.toml" ]]; then
    echo "Error: .provider/config.toml not found (LLM provider config required)"
    exit 1
fi

# --- Run ---

echo "=== work-recap: ${START_YEAR} ~ ${CURRENT_YEAR} (workers=${WORKERS}, batch=on) ==="

failed_years=()

for (( year=START_YEAR; year<=CURRENT_YEAR; year++ )); do
    echo ""
    echo "--- ${year} ---"
    if workrecap run --yearly "$year" --batch -w "$WORKERS"; then
        echo "✓ ${year} done"
    else
        echo "✗ ${year} failed (continuing...)"
        failed_years+=("$year")
    fi
done

echo ""
if [[ ${#failed_years[@]} -eq 0 ]]; then
    echo "=== All years complete (${START_YEAR}~${CURRENT_YEAR}) ==="
else
    echo "=== Done with ${#failed_years[@]} failure(s): ${failed_years[*]} ==="
    exit 1
fi
