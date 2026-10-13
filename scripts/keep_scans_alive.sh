#!/usr/bin/env bash
# Ping GitHub only at NSE scan slots. GitHub bills 1 minute per job even for skips.
set -euo pipefail

export TZ=Asia/Kolkata
HOUR=$((10#$(date +%H)))
MINUTE=$((10#$(date +%M)))
DOW="$(date +%u)" # 1=Mon … 7=Sun

if [[ "$DOW" -ge 6 ]]; then
  exit 0
fi

# Every weekday: 09:30–15:30 every 15 minutes, plus 15:40.
# Tue=2 and Thu=4 also ping 15:10.
slot=0
if [[ "$HOUR" -eq 9 && ( "$MINUTE" -eq 30 || "$MINUTE" -eq 45 ) ]]; then
  slot=1
elif [[ "$HOUR" -ge 10 && "$HOUR" -le 14 && ( "$MINUTE" -eq 0 || "$MINUTE" -eq 15 || "$MINUTE" -eq 30 || "$MINUTE" -eq 45 ) ]]; then
  slot=1
elif [[ "$HOUR" -eq 15 && ( "$MINUTE" -eq 0 || "$MINUTE" -eq 15 || "$MINUTE" -eq 30 || "$MINUTE" -eq 40 ) ]]; then
  slot=1
elif [[ ( "$DOW" -eq 2 || "$DOW" -eq 4 ) && "$HOUR" -eq 15 && "$MINUTE" -eq 10 ]]; then
  slot=1
fi

if [[ "$slot" -ne 1 ]]; then
  exit 0
fi

if ! command -v gh >/dev/null; then
  echo "gh not on PATH" >&2
  exit 1
fi

REPO="${GITHUB_REPOSITORY:-AbhilashMunnur/nifty-index-trade}"
gh api --method POST "repos/${REPO}/dispatches" -f event_type='nifty-scan'
echo "$(date '+%Y-%m-%d %H:%M:%S') dispatched nifty-scan"
