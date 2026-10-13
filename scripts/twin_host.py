#!/usr/bin/env python3
"""Decide whether this checkout should scan.

Repo 1 always scans. Repo 2 waits a moment to see repo 1's job. If GitHub
refused that job, repo 2 scans. If repo 1's job is running or already
finished, repo 2 leaves the slot alone so Telegram is sent once.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.twin import PRIMARY_REPO, STANDBY_REPO, classify_run, decide

LOOKBACK = timedelta(minutes=4)
APPEAR_SECONDS = 150
FINISH_SECONDS = 8 * 60
POLL_SECONDS = 15


def emit(scan: bool) -> None:
    line = f"scan={'true' if scan else 'false'}"
    print(line)
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def _token() -> str:
    return (os.environ.get("GH_TOKEN") or os.environ.get("TWIN_TOKEN") or "").strip()


def _get(url: str, token: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "nifty-twin",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())


def _parse_time(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _recent_runs(workflow: str, token: str, now: datetime) -> list[dict]:
    url = (
        "https://api.github.com/repos/"
        f"{PRIMARY_REPO}/actions/workflows/{workflow}/runs?per_page=15"
    )
    payload = _get(url, token)
    runs = []
    for run in payload.get("workflow_runs") or []:
        created = _parse_time(str(run.get("created_at") or ""))
        if now - created > LOOKBACK:
            continue
        event = str(run.get("event") or "")
        if event not in {"schedule", "workflow_dispatch", "repository_dispatch"}:
            continue
        runs.append(run)
    return runs


def _jobs(run_id: int, token: str) -> list[dict]:
    url = f"https://api.github.com/repos/{PRIMARY_REPO}/actions/runs/{run_id}/jobs"
    payload = _get(url, token)
    return list(payload.get("jobs") or [])


def _kinds(runs: list[dict], token: str) -> list[str]:
    kinds = []
    for run in runs:
        jobs: list[dict] = []
        if str(run.get("status") or "") == "completed" and str(run.get("conclusion") or "") != "success":
            try:
                jobs = _jobs(int(run["id"]), token)
            except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as exc:
                print(f"Could not read jobs for run {run.get('id')}: {exc}")
                jobs = []
        kinds.append(classify_run(run, jobs))
    return kinds


def wait_for_primary(workflow: str, token: str) -> bool:
    """True when repo 2 should scan."""
    started = time.monotonic()
    while True:
        now = datetime.now(timezone.utc)
        try:
            runs = _recent_runs(workflow, token, now)
            kinds = _kinds(runs, token)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            print(f"Could not read repo 1 workflow runs: {exc}")
            if time.monotonic() - started >= APPEAR_SECONDS:
                print("Repo 1 did not answer. Scanning from repo 2.")
                return True
            time.sleep(POLL_SECONDS)
            continue
        elapsed = time.monotonic() - started
        choice = decide(kinds, appear_deadline_passed=elapsed >= APPEAR_SECONDS)
        print(f"repo 1 {workflow}: {kinds or 'no run yet'} -> {choice}")
        if choice == "scan":
            print("GitHub refused repo 1. Scanning from repo 2.")
            return True
        if choice == "yield":
            print("Repo 1 has this slot. Repo 2 will not send a second alert.")
            return False
        if elapsed >= FINISH_SECONDS:
            print("Repo 1 still has this slot open. Repo 2 will not scan.")
            return False
        time.sleep(POLL_SECONDS)


def main() -> None:
    workflow = "scan.yml"
    if "--workflow" in sys.argv:
        workflow = sys.argv[sys.argv.index("--workflow") + 1]
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if repository != STANDBY_REPO:
        print(f"{repository or 'this checkout'} scans as repo 1.")
        emit(True)
        return
    token = _token()
    if not token:
        print("TWIN_TOKEN is not set. Scanning from repo 2.")
        emit(True)
        return
    emit(wait_for_primary(workflow, token))


if __name__ == "__main__":
    main()
