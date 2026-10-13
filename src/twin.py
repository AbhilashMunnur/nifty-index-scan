"""Which GitHub repo scans, and when the public copy takes over.

Repo 1 is the private repo. It scans while GitHub Actions still accepts its
jobs. Repo 2 is the public repo. It scans only after GitHub refuses repo 1,
which is what happens once the monthly minutes are used up or the spending
limit blocks new jobs.
"""

from __future__ import annotations

PRIMARY_REPO = "AbhilashMunnur/nifty-index-trade"
STANDBY_REPO = "AbhilashMunnur/nifty-index-scan"


def other_repo(repository: str) -> str:
    if repository == PRIMARY_REPO:
        return STANDBY_REPO
    if repository == STANDBY_REPO:
        return PRIMARY_REPO
    raise ValueError(f"{repository} is not one of the twin scan repos")


def job_started(job: dict) -> bool:
    return bool(job.get("steps"))


def classify_run(run: dict, jobs: list[dict]) -> str:
    """Return pending, done, or blocked.

    A refused Actions job finishes in a few seconds with no steps. That is the
    monthly-quota and spending-limit failure. A job that actually started is
    repo 1's attempt, even if a later step failed.
    """
    status = str(run.get("status") or "")
    if status != "completed":
        return "pending"
    conclusion = str(run.get("conclusion") or "")
    if conclusion == "success":
        return "done"
    if conclusion == "startup_failure":
        return "blocked"
    started = any(job_started(job) for job in jobs)
    if conclusion == "failure" and not started:
        return "blocked"
    return "done"


def decide(kinds: list[str], *, appear_deadline_passed: bool) -> str:
    """Return scan, yield, or wait for the standby repo."""
    if "done" in kinds:
        return "yield"
    if "pending" in kinds:
        return "wait"
    if "blocked" in kinds:
        return "scan"
    if not appear_deadline_passed:
        return "wait"
    return "scan"
