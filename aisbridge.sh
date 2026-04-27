#!/usr/bin/env bash
# aisbridge.sh — run the AIS→TAK bridge directly (no Docker)
#
# Reads config from .env in the same directory, creates a Python venv,
# installs dependencies, and launches aisstream_bridge.py.
#
# Usage:
#   chmod +x aisbridge.sh
#   ./aisbridge.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
VENV_DIR="${SCRIPT_DIR}/.venv"

# ── Banner ───────────────────────────────────────────────────────────────────

cat << 'EOF'

  █████╗ ██╗███████╗     ██████╗ ██████╗ ████████╗
 ██╔══██╗██║██╔════╝    ██╔════╝██╔═══██╗╚══██╔══╝
 ███████║██║███████╗    ██║     ██║   ██║   ██║
 ██╔══██║██║╚════██║    ██║     ██║   ██║   ██║
 ██║  ██║██║███████║    ╚██████╗╚██████╔╝   ██║
 ╚═╝  ╚═╝╚═╝╚══════╝    ╚═════╝ ╚═════╝    ╚═╝

     Get local ship traffic to ATAK

EOF

# ── Preflight checks ─────────────────────────────────────────────────────────

if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 is required but not found."
    exit 1
fi

# ── Ensure .env exists ────────────────────────────────────────────────────────

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "[*] No .env found — creating one from template..."
    cp "${SCRIPT_DIR}/.env-TEMPLATE" "${ENV_FILE}"
fi

# ── Load .env ─────────────────────────────────────────────────────────────────

set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

# ── Prompt for API key if missing or still placeholder ────────────────────────

if [[ -z "${AISSTREAM_API_KEY:-}" || "${AISSTREAM_API_KEY}" == "<CHANGE_ME>" ]]; then
    echo ""
    echo "  An aisstream.io API key is required (free at https://aisstream.io — sign in with GitHub)."
    echo ""
    read -rp "  Enter your aisstream.io API key: " input_key
    if [[ -z "${input_key}" ]]; then
        echo "ERROR: No API key entered. Exiting."
        exit 1
    fi
    # Persist into .env so the user isn't prompted again
    sed -i.bak "s|AISSTREAM_API_KEY=.*|AISSTREAM_API_KEY=${input_key}|" "${ENV_FILE}" && rm -f "${ENV_FILE}.bak"
    AISSTREAM_API_KEY="${input_key}"
    echo "  API key saved to .env."
    echo ""
fi

# ── Validate remaining required vars ─────────────────────────────────────────

if [[ -z "${TAK_HOST:-}" ]]; then
    echo "ERROR: TAK_HOST is not set in .env"
    exit 1
fi

# ── Virtual environment ───────────────────────────────────────────────────────

if [[ ! -d "${VENV_DIR}" ]]; then
    echo "[*] Creating Python venv..."
    python3 -m venv "${VENV_DIR}"
fi

echo "[*] Installing dependencies..."
"${VENV_DIR}/bin/pip" install --quiet --upgrade websockets

# ── Run ───────────────────────────────────────────────────────────────────────

echo "[*] Starting AIS→TAK bridge (no Docker)"
echo "    TAK Server   : ${TAK_HOST}:${TAK_PORT:-8087}"
echo "    Bounding box : lat ${BBOX_LAT_MIN}..${BBOX_LAT_MAX}, lon ${BBOX_LON_MIN}..${BBOX_LON_MAX}"
echo "    Update rate  : ${UPDATE_RATE:-60}s"
echo ""

exec "${VENV_DIR}/bin/python3" "${SCRIPT_DIR}/aisstream_bridge.py"
