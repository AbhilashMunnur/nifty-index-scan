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

# Tue=2 and Thu=4 keep 15-minute slots, including 15:10 and 15:15.
# Mon=1, Wed=3, Fri=5 are every 30 minutes. 15:40 close is every weekday.
quarter=0
if [[ "$DOW" -eq 2 || "$DOW" -eq 4 ]]; then
  quarter=1
fi

slot=0
if [[ "$HOUR" -eq 15 && "$MINUTE" -eq 40 ]]; then
  slot=1
elif [[ "$quarter" -eq 1 && "$HOUR" -eq 15 && ( "$MINUTE" -eq 10 || "$MINUTE" -eq 15 ) ]]; then
  slot=1
elif [[ "$HOUR" -ge 9 && "$HOUR" -le 15 ]]; then
  if [[ "$quarter" -eq 1 ]]; then
    if [[ "$MINUTE" -eq 0 || "$MINUTE" -eq 15 || "$MINUTE" -eq 30 || "$MINUTE" -eq 45 ]]; then
      if [[ "$HOUR" -gt 9 || "$MINUTE" -ge 30 ]]; then
        if [[ ! ( "$HOUR" -eq 15 && "$MINUTE" -eq 45 ) ]]; then
          slot=1
        fi
      fi
    fi
  elif [[ "$HOUR" -eq 9 && "$MINUTE" -eq 30 ]]; then
    slot=1
  elif [[ "$HOUR" -ge 10 && "$HOUR" -le 14 && ( "$MINUTE" -eq 0 || "$MINUTE" -eq 30 ) ]]; then
    slot=1
  elif [[ "$HOUR" -eq 15 && ( "$MINUTE" -eq 0 || "$MINUTE" -eq 30 ) ]]; then
    slot=1
  fi
fi

if [[ "$slot" -ne 1 ]]; then
  exit 0
fi

if ! command -v gh >/dev/null; then
  echo "gh not on PATH" >&2
  exit 1
fi

REPO="${GITHUB_REPOSITORY:-AbhilashMunnur/nifty-index-scan}"
gh api --method POST "repos/${REPO}/dispatches" -f event_type='nifty-scan'
echo "$(date '+%Y-%m-%d %H:%M:%S') dispatched nifty-scan"
