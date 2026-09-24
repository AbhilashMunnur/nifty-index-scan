# Live scan kick (Mac does not need to stay awake)

GitHub Free private repos share **2,000 Actions minutes/month**. Every
workflow run is billed at least 1 minute, even a 3-second skip.

## Minute budget (Nifty)

NSE slots only: **09:30–15:30 IST every 15 minutes, plus 15:40** (26/day).
Real scans have been ~2 minutes. About 22 trading days ≈ **1,150 min/month**.
Each Telegram includes a broker-style **P&L snapshot**.
That leaves room for a few missed retries — not for 5-minute polling or a
runner that `sleep`s until the next slot.

Do **not** ping at 08:00 or every 5 minutes. Those skip-jobs alone exceed 2,000.

## Required: cron-job.org (runs while you sleep)

Free account: https://console.cron-job.org/signup

1. Sign up, then Console → **Settings** → create an **API key**.
2. On this Mac, in a terminal (the key is typed hidden, not into chat):

```bash
cd "/Users/abhilashmunnur/Nifty index trade"
python3 scripts/provision_cronjob_org.py
```

That creates POSTs at **09:30, 09:45, … 15:30, 15:40 IST, Mon–Fri** to:

`https://api.github.com/repos/AbhilashMunnur/nifty-index-scan/dispatches`

Body: `{"event_type":"nifty-scan"}`. Auth is your current `gh` token (`repo` scope).

Re-run the same script after `gh auth login` if the GitHub token is rotated.
It disables the old every-5-minutes job.

## Already in GitHub

- `scan.yml` cron is the same 26 IST slots (UTC: `04:00–10:10`, weekdays).
- No self-chain sleep job and no 08:30 warmup (those billed tens of minutes).
- Slot guard still skips a duplicate if both cron-job.org and GitHub fire.

## Optional: this Mac, only if it is already awake

```bash
./scripts/install_mac_scan_watch.sh
```

The script no-ops unless the clock is on a 15-minute slot (or 15:40). Unload it
if you do not want local pings:

```bash
launchctl bootout "gui/$(id -u)/com.niftyindextrade.scanwatch"
```
