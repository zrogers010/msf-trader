#!/usr/bin/env bash
# Scheduled daily run: compute + place the RSI(2) swing plan on the Alpaca PAPER account.
# Cadence: Mon-Fri ~15:45 America/New_York. The market-clock guard inside the CLI
# skips any day the market is closed (weekends/holidays), so over-scheduling is safe.
#
# Sizing here is fixed $1000/trade, up to 10 concurrent names (the validated default).
# Edit the flags below to change sizing. Output is appended to a dated log file.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
mkdir -p logs
LOG="logs/swing-$(date +%Y%m%d).log"

# shellcheck disable=SC1091
source .venv/bin/activate
{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') ====="
  msf-trader swing-plan --broker alpaca --dollars 1000 --slots 10
  echo ""
} >>"$LOG" 2>&1
