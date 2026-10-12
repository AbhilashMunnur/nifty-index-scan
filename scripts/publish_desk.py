"""Push the public scan desk. Copies the site and generated readings only."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.desk import build_desk

PUBLIC_REPO = "git@github.com:AbhilashMunnur/nifty-alert-desk.git"
SITE_FILES = ("index.html", "styles.css", "app.js", ".nojekyll")
GITHUB_HOST_KEY = (
    "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    desk = build_desk(ROOT)
    payload = json.dumps(desk, indent=2) + "\n"
    out = args.out or (ROOT / "site" / "data" / "desk.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(payload, encoding="utf-8")
    print(f"Wrote {out}")
    if args.push:
        push_public(payload)


def push_public(payload: str) -> None:
    key_text = os.environ.get("DESK_DEPLOY_KEY", "").strip()
    if not key_text:
        raise SystemExit("DESK_DEPLOY_KEY is not set. The public page was not updated.")
    if "BEGIN" not in key_text or "PRIVATE KEY" not in key_text:
        raise SystemExit("DESK_DEPLOY_KEY is not a private key. The public page was not updated.")

    work = Path(tempfile.mkdtemp(prefix="nifty-alert-desk-"))
    key_path = work / "deploy_key"
    known_hosts = work / "known_hosts"
    try:
        key_path.write_text(key_text + "\n", encoding="utf-8")
        key_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        known_hosts.write_text(GITHUB_HOST_KEY + "\n", encoding="utf-8")
        ssh = (
            f"ssh -i {key_path} -o IdentitiesOnly=yes "
            f"-o UserKnownHostsFile={known_hosts} -o StrictHostKeyChecking=yes"
        )
        clone = work / "repo"
        _run(["git", "clone", "--depth", "1", PUBLIC_REPO, str(clone)], ssh)
        for name in SITE_FILES:
            source = ROOT / "site" / name
            if source.exists():
                shutil.copyfile(source, clone / name)
        data_dir = clone / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "desk.json").write_text(payload, encoding="utf-8")
        _run(["git", "add", "index.html", "styles.css", "app.js", ".nojekyll", "data/desk.json"], ssh, clone)
        status = _run(["git", "status", "--porcelain"], ssh, clone)
        if not status.strip():
            print("Public desk already matches this scan.")
            return
        _run(
            [
                "git",
                "-c",
                "user.name=Abhilash Munnur",
                "-c",
                "user.email=70260593+AbhilashMunnur@users.noreply.github.com",
                "commit",
                "-m",
                "Update the public scan desk.",
            ],
            ssh,
            clone,
        )
        _run(["git", "push", "origin", "HEAD"], ssh, clone)
        print("Public desk updated.")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _run(command: list[str], ssh: str, cwd: Path | None = None) -> str:
    env = os.environ.copy()
    env["GIT_SSH_COMMAND"] = ssh
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )
    return completed.stdout


if __name__ == "__main__":
    main()
