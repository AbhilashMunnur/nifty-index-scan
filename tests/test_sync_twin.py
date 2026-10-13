import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import sync_twin


def test_newer_ledger_and_later_slot_win(tmp_path):
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    (left / "nifty_fut_flow_mark.json").write_text(
        '{"updated_at": "2026-10-13T15:30:00+05:30", "side": "left"}\n',
        encoding="utf-8",
    )
    (right / "nifty_fut_flow_mark.json").write_text(
        '{"updated_at": "2026-10-13T10:00:00+05:30", "side": "right"}\n',
        encoding="utf-8",
    )
    (left / "last_scan_slot.txt").write_text("2026-10-13T10:00:00+05:30\n", encoding="utf-8")
    (right / "last_scan_slot.txt").write_text("2026-10-13T15:30:00+05:30\n", encoding="utf-8")

    sync_twin.merge_data(left, right)

    left_mark = (left / "nifty_fut_flow_mark.json").read_text(encoding="utf-8")
    right_mark = (right / "nifty_fut_flow_mark.json").read_text(encoding="utf-8")
    assert "15:30:00" in left_mark
    assert left_mark == right_mark
    assert (left / "last_scan_slot.txt").read_text(encoding="utf-8").strip() == "2026-10-13T15:30:00+05:30"
    assert (right / "last_scan_slot.txt").read_text(encoding="utf-8") == (
        left / "last_scan_slot.txt"
    ).read_text(encoding="utf-8")
