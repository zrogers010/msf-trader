#!/usr/bin/env bash
# One-time setup on a fresh machine (e.g. the Mac Mini).
# Creates a venv, installs the package, and verifies the strategy + broker wiring.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$(pwd)"
echo "Repo: $REPO"

command -v python3 >/dev/null || { echo "ERROR: python3 not found"; exit 1; }

[ -d .venv ] || python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip >/dev/null
pip install -e .

if [ ! -f .env ]; then
  cp .env.example .env
  echo ">> Created .env from template. Fill in ALPACA_PAPER_KEY_ID / ALPACA_PAPER_SECRET_KEY, then re-run."
  exit 1
fi

echo ">> Running swing tests..."
python -m pytest tests/test_swing.py -q

echo ""
echo ">> Setup complete. Verify connectivity (places nothing):"
echo "     source .venv/bin/activate && msf-trader swing-plan --broker dry"
echo ">> Then test the live paper path during market hours:"
echo "     msf-trader swing-plan --broker alpaca --dollars 1000 --slots 10"
