#!/usr/bin/env python3
"""Create or update cron-job.org pings on the weekday scan grid.

GitHub bills a full minute for every workflow run, including slot-guard skips.

Tuesday and Thursday: 09:30–15:30 every 15 min, plus 15:10, 15:15, and 15:40.
Monday, Wednesday, and Friday: 09:30–15:30 every 30 min, plus 15:40.
No 15:10 or 15:15 on Mon/Wed/Fri.

Usage:
  CRON_JOB_ORG_API_KEY=... python3 scripts/provision_cronjob_org.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

API = "https://api.cron-job.org"
REPO = os.environ.get("GITHUB_REPOSITORY", "AbhilashMunnur/nifty-index-scan")
OLD_TITLE = "Nifty index trade — scan kick"
DISPATCH_URL = f"https://api.github.com/repos/{REPO}/dispatches"
# cron-job.org wdays: 1=Mon … 5=Fri, matching the jobs that already ran on weekdays.
SPECS = (
    ("Nifty scan — MWF 09:30 IST", [9], [30], [1, 3, 5]),
    ("Nifty scan — MWF 10:00–14:30 IST", list(range(10, 15)), [0, 30], [1, 3, 5]),
    ("Nifty scan — MWF 15:00/15:30/15:40 IST", [15], [0, 30, 40], [1, 3, 5]),
    ("Nifty scan — Tue/Thu 09:30/09:45 IST", [9], [30, 45], [2, 4]),
    ("Nifty scan — Tue/Thu 10:00–14:45 IST", list(range(10, 15)), [0, 15, 30, 45], [2, 4]),
    ("Nifty scan — Tue/Thu 15:00–15:40 IST", [15], [0, 10, 15, 30, 40], [2, 4]),
)
RETIRED_TITLES = {
    OLD_TITLE,
    "Nifty scan — 09:30/09:45 IST",
    "Nifty scan — 10:00–14:45 IST",
    "Nifty scan — 15:00–15:40 IST",
}


def gh_token() -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token.strip()
    try:
        out = subprocess.check_output(["gh", "auth", "token"], text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        sys.exit(f"Need a GitHub token (gh auth login): {exc}")
    token = out.strip()
    if not token:
        sys.exit("gh auth token was empty")
    return token


def api_key() -> str:
    key = os.environ.get("CRON_JOB_ORG_API_KEY", "").strip()
    if key:
        return key
    try:
        import getpass
    except ImportError:
        sys.exit("Set CRON_JOB_ORG_API_KEY")
    key = getpass.getpass("cron-job.org API key (Settings → API): ").strip()
    if not key:
        sys.exit("No API key given")
    return key


def request(method: str, path: str, key: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        sys.exit(f"cron-job.org {method} {path} failed ({exc.code}): {body}")
    if not raw:
        return {}
    return json.loads(raw)


def job_body(
    github_token: str,
    title: str,
    hours: list[int],
    minutes: list[int],
    wdays: list[int],
) -> dict:
    return {
        "job": {
            "enabled": True,
            "title": title,
            "saveResponses": False,
            "url": DISPATCH_URL,
            "requestMethod": 1,
            "schedule": {
                "timezone": "Asia/Kolkata",
                "expiresAt": 0,
                "hours": hours,
                "mdays": [-1],
                "minutes": minutes,
                "months": [-1],
                "wdays": wdays,
            },
            "extendedData": {
                "headers": {
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {github_token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                "body": json.dumps({"event_type": "nifty-scan"}),
            },
        }
    }


def disable_job(key: str, job_id: int, title: str) -> None:
    request(
        "PATCH",
        f"/jobs/{job_id}",
        key,
        {"job": {"enabled": False, "title": title}},
    )
    print(f"Disabled old 5-minute job {job_id} ({title})")


def upsert(
    key: str,
    token: str,
    existing: list[dict],
    title: str,
    hours: list[int],
    minutes: list[int],
    wdays: list[int],
) -> None:
    payload = job_body(token, title, hours, minutes, wdays)
    match = next((j for j in existing if j.get("title") == title), None)
    if match:
        job_id = match["jobId"]
        request("PATCH", f"/jobs/{job_id}", key, payload)
        print(f"Updated cron-job.org job {job_id} ({title})")
    else:
        created = request("PUT", "/jobs", key, payload)
        print(f"Created cron-job.org job {created.get('jobId')} ({title})")


def main() -> None:
    key = api_key()
    token = gh_token()
    existing = request("GET", "/jobs", key).get("jobs") or []
    for job in existing:
        title = str(job.get("title") or "")
        if title in RETIRED_TITLES and job.get("enabled", True):
            disable_job(key, job["jobId"], title)
    existing = request("GET", "/jobs", key).get("jobs") or []
    for title, hours, minutes, wdays in SPECS:
        upsert(key, token, existing, title, hours, minutes, wdays)
        existing = request("GET", "/jobs", key).get("jobs") or []
    print("Tue/Thu: every 15 min including 15:10 and 15:15, plus 15:40")
    print("Mon/Wed/Fri: every 30 min from 09:30–15:30, plus 15:40")
    print(f"Target:   POST {DISPATCH_URL} event_type=nifty-scan")


if __name__ == "__main__":
    main()
