#!/usr/bin/env python3
"""Keep nifty-index-trade and nifty-index-scan on the same files.

A push copies this repo onto the other one, except data files: the newer
ledger always wins so a standby scan is not overwritten by an older book.
The hourly run on the public repo does the same merge both ways. That covers
the hours when the private repo can no longer start Actions jobs.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import keep_ledger
from src.twin import PRIMARY_REPO, other_repo

COMMIT_MESSAGE = "Sync the twin scan repo [skip ci]"
AUTHOR_NAME = "Abhilash Munnur"
AUTHOR_EMAIL = "70260593+AbhilashMunnur@users.noreply.github.com"


def _token() -> str:
    token = (os.environ.get("GH_TOKEN") or os.environ.get("TWIN_TOKEN") or "").strip()
    if token:
        return token
    completed = subprocess.run(["gh", "auth", "token"], check=True, text=True, capture_output=True)
    token = completed.stdout.strip()
    if not token:
        raise SystemExit("No GitHub token. Set TWIN_TOKEN or run gh auth login.")
    return token


def _git_env(token: str, askpass: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["TWIN_GIT_TOKEN"] = token
    env["GIT_ASKPASS"] = str(askpass)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GH_TOKEN"] = token
    return env


def _run(command: list[str], cwd: Path, env: dict[str, str]) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise SystemExit(detail or f"{command[0]} failed")
    return completed.stdout


def _tracked(repo: Path, env: dict[str, str]) -> list[str]:
    out = _run(["git", "ls-files"], repo, env)
    return [line for line in out.splitlines() if line]


def _commit_time(repo: Path, rel: str, env: dict[str, str]) -> int:
    completed = subprocess.run(
        ["git", "log", "-1", "--format=%ct", "--", rel],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
    )
    text = completed.stdout.strip()
    return int(text) if text else 0


def _read(repo: Path, rel: str) -> bytes | None:
    path = repo / rel
    if not path.is_file():
        return None
    return path.read_bytes()


def _place(repo: Path, rel: str, content: bytes | None) -> None:
    path = repo / rel
    if content is None:
        if path.exists():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() == content:
        return
    path.write_bytes(content)


def _copy_data(src: Path, dest: Path) -> None:
    src_files = {path.relative_to(src) for path in src.rglob("*") if path.is_file()}
    dest_files = {path.relative_to(dest) for path in dest.rglob("*") if path.is_file()} if dest.exists() else set()
    for rel in dest_files - src_files:
        (dest / rel).unlink()
    for rel in src_files:
        payload = (src / rel).read_bytes()
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file() or target.read_bytes() != payload:
            target.write_bytes(payload)


def merge_data(left: Path, right: Path) -> None:
    """Fold both ledgers together and write the result to both trees."""
    left.mkdir(parents=True, exist_ok=True)
    right.mkdir(parents=True, exist_ok=True)
    markers: dict[Path, list[str]] = {}
    for root in (left, right):
        for path in root.rglob("*.txt"):
            rel = path.relative_to(root)
            markers.setdefault(rel, []).append(path.read_text(encoding="utf-8").strip())
    saved = sys.argv
    try:
        sys.argv = ["keep_ledger.py", str(right), str(left)]
        keep_ledger.main()
    finally:
        sys.argv = saved
    for rel, values in markers.items():
        present = [value for value in values if value]
        if not present:
            continue
        best = max(present) + "\n"
        for root in (left, right):
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(best, encoding="utf-8")
    _copy_data(left, right)


def merge_source(left: Path, right: Path, *, left_wins_ties: bool, env: dict[str, str]) -> None:
    names = set(_tracked(left, env)) | set(_tracked(right, env))
    for rel in sorted(names):
        if rel == "data" or rel.startswith("data/"):
            continue
        left_time = _commit_time(left, rel, env)
        right_time = _commit_time(right, rel, env)
        left_bytes = _read(left, rel)
        right_bytes = _read(right, rel)
        if left_bytes == right_bytes:
            continue
        if left_time > right_time or (left_time == right_time and left_wins_ties):
            winner = left_bytes
        else:
            winner = right_bytes
        _place(left, rel, winner)
        _place(right, rel, winner)


def _commit_and_push(repo: Path, env: dict[str, str], remote: str) -> None:
    _run(["git", "add", "-A"], repo, env)
    status = _run(["git", "status", "--porcelain"], repo, env)
    if not status.strip():
        print(f"{remote}: already in sync")
        return
    _run(
        [
            "git",
            "-c",
            f"user.name={AUTHOR_NAME}",
            "-c",
            f"user.email={AUTHOR_EMAIL}",
            "commit",
            "-m",
            COMMIT_MESSAGE,
        ],
        repo,
        env,
    )
    _run(["git", "push", "origin", "HEAD:main"], repo, env)
    print(f"{remote}: synced")


def _clone(repository: str, dest: Path, env: dict[str, str], *, full: bool) -> None:
    command = ["git", "clone", f"https://github.com/{repository}.git", str(dest)]
    if not full:
        command[2:2] = ["--depth", "1"]
    _run(command, dest.parent, env)


def sync(mode: str) -> None:
    repository = os.environ.get("GITHUB_REPOSITORY", PRIMARY_REPO)
    other = other_repo(repository)
    token = _token()
    work = Path(tempfile.mkdtemp(prefix="nifty-twin-"))
    askpass = work / "askpass.sh"
    askpass.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        '  *Username*) echo "x-access-token" ;;\n'
        '  *) echo "$TWIN_GIT_TOKEN" ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    askpass.chmod(stat.S_IRUSR | stat.S_IXUSR)
    env = _git_env(token, askpass)
    try:
        if mode == "reconcile":
            subprocess.run(["git", "fetch", "--unshallow"], cwd=ROOT, env=env, capture_output=True)
        other_root = work / "other"
        _clone(other, other_root, env, full=mode == "reconcile")
        if mode == "propagate":
            for rel in _tracked(ROOT, env):
                if rel == "data" or rel.startswith("data/"):
                    continue
                _place(other_root, rel, _read(ROOT, rel))
            for rel in _tracked(other_root, env):
                if rel == "data" or rel.startswith("data/"):
                    continue
                if rel not in set(_tracked(ROOT, env)):
                    _place(other_root, rel, None)
        else:
            merge_source(
                ROOT,
                other_root,
                left_wins_ties=repository == PRIMARY_REPO,
                env=env,
            )
        merge_data(ROOT / "data", other_root / "data")
        _commit_and_push(other_root, env, other)
        if mode == "reconcile":
            _commit_and_push(ROOT, env, repository)
        else:
            # A newer ledger on the other side was merged back into this checkout.
            _commit_and_push(ROOT, env, repository)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    mode = "reconcile" if "--reconcile" in sys.argv else "propagate"
    if os.environ.get("GITHUB_EVENT_NAME") == "schedule":
        mode = "reconcile"
    sync(mode)


if __name__ == "__main__":
    main()
