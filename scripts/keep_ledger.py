#!/usr/bin/env python3
"""Merge this scan's saved files onto the latest main checkout.

A later job must not wipe an earlier slot out of the day history.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _stamp(payload) -> str:
    if isinstance(payload, dict):
        return str(payload.get("updated_at") or "")
    return ""


def _merge_history(kept: dict, dest: dict) -> dict:
    out = dict(dest)
    out["date"] = kept.get("date") or dest.get("date")
    for key in set(kept) | set(dest):
        if key == "date":
            continue
        by_slot: dict[str, dict] = {}
        for source in (dest.get(key), kept.get(key)):
            if not isinstance(source, list):
                continue
            for row in source:
                if not isinstance(row, dict):
                    continue
                slot = str(row.get("slot") or row.get("updated_at") or "")
                current = by_slot.get(slot)
                if current is None or _stamp(row) >= _stamp(current):
                    by_slot[slot] = row
        if by_slot:
            out[key] = sorted(by_slot.values(), key=_stamp)
    return out


def main() -> int:
    if len(sys.argv) < 2:
        return 0
    kept_root = Path(sys.argv[1])
    dest_root = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("data")
    if not kept_root.exists():
        return 0
    dest_root.mkdir(parents=True, exist_ok=True)
    for path in kept_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(kept_root)
        target = dest_root / rel
        if "scan_history" in path.parts and path.suffix == ".json":
            target.parent.mkdir(parents=True, exist_ok=True)
            kept_body = _load(path) or {}
            dest_body = _load(target) or {}
            if isinstance(kept_body, dict) and isinstance(dest_body, dict):
                merged = _merge_history(kept_body, dest_body)
                target.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
            else:
                target.write_bytes(path.read_bytes())
            continue
        if path.suffix == ".json":
            kept_body = _load(path)
            dest_body = _load(target) if target.exists() else None
            if _stamp(dest_body) > _stamp(kept_body):
                continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
