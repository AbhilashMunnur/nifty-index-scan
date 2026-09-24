#!/usr/bin/env bash
# Optional: store a token for manual workflow_dispatch / one-off kicks.
# Live scans are cron-job.org + scan.yml IST crons (no self-chain sleep).
set -euo pipefail

REPO="${GITHUB_REPOSITORY:-AbhilashMunnur/nifty-index-scan}"

if ! command -v gh >/dev/null; then
  echo "Install GitHub CLI first: https://cli.github.com/"
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "Run: gh auth login"
  exit 1
fi

TOKEN="$(gh auth token)"
if [[ -z "$TOKEN" ]]; then
  echo "Could not read a GitHub token from gh."
  exit 1
fi

echo "Setting SCAN_DISPATCH_TOKEN on $REPO ..."
printf '%s' "$TOKEN" | gh secret set SCAN_DISPATCH_TOKEN --repo "$REPO"

echo "Triggering a test nifty-scan dispatch ..."
gh api --method POST "repos/$REPO/dispatches" -f event_type='nifty-scan'

cat <<EOF

Done. Scans run at 09:30–15:30 IST every 15 min plus 15:40, Mon–Fri.
Each Telegram includes a P&L snapshot.

Keep cron-job.org on that same grid (not every 5 minutes):
  python3 scripts/provision_cronjob_org.py
See scripts/setup_external_cron.md

Watch runs: gh run list --repo $REPO --workflow=scan.yml --limit 5
EOF
