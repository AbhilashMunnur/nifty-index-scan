import json
from datetime import datetime
from pathlib import Path

from src.fut_flow import save_mark
from src.oi_flow import save_premium_mark
from src.pcr import BandPcr, StrikePcr
from src.scan_history import append_scan, history_path, prune_old_scans, slot_key


def test_same_slot_replaces_and_the_next_slot_is_kept(tmp_path, monkeypatch):
    monkeypatch.setattr("src.scan_history.HISTORY_DIR", tmp_path)
    first = {"updated_at": "2026-10-08 15:00:20", "oi": 1}
    second = {"updated_at": "2026-10-08 15:02:10", "oi": 2}
    later = {"updated_at": "2026-10-08 15:10:05", "oi": 3}
    append_scan("nifty_oi", first)
    append_scan("nifty_oi", second)
    append_scan("nifty_oi", later)
    rows = json.loads(history_path("2026-10-08").read_text())["nifty_oi"]
    assert [row["oi"] for row in rows] == [2, 3]
    assert rows[0]["slot"] == "2026-10-08 15:00"
    assert rows[1]["slot"] == "2026-10-08 15:10"
    assert slot_key(datetime(2026, 10, 8, 15, 42)) == "2026-10-08 15:40"


def test_option_and_futures_saves_keep_their_own_rows(tmp_path, monkeypatch):
    monkeypatch.setattr("src.scan_history.HISTORY_DIR", tmp_path)
    band = BandPcr(
        expiry="2026-10-08",
        atm=22700,
        step=50,
        lot_size=65,
        strikes=[StrikePcr("ATM", 22700, 10, 20, call_ltp=1.0, put_ltp=2.0)],
    )
    save_premium_mark(band, path=tmp_path / "band_scan_mark.json")
    save_premium_mark(band, path=tmp_path / "sensex_band_scan_mark.json")
    save_mark(
        tradingsymbol="NIFTY27OCT26FUT",
        token="1",
        expiry="2026-10-27",
        lot_size=65,
        price=100.0,
        oi=65,
        path=tmp_path / "nifty_fut_flow_mark.json",
    )
    save_mark(
        tradingsymbol="NIFTY27OCT26FUT",
        token="1",
        expiry="2026-10-27",
        lot_size=65,
        price=100.0,
        oi=65,
        path=tmp_path / "nifty_fut_day_open.json",
    )
    day = datetime.now().strftime("%Y-%m-%d")
    body = json.loads((tmp_path / f"{day}.json").read_text())
    assert len(body["nifty_oi"]) == 1
    assert len(body["sensex_oi"]) == 1
    assert len(body["nifty_fut"]) == 1
    assert Path(tmp_path / "nifty_fut_day_open.json").exists()


def test_a_scan_day_is_deleted_once_it_is_two_days_old(tmp_path):
    from datetime import date

    (tmp_path / "2026-10-06.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "2026-10-07.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "notes.json").write_text("{}\n", encoding="utf-8")
    removed = prune_old_scans(tmp_path, today=date(2026, 10, 8))
    assert [path.name for path in removed] == ["2026-10-06.json"]
    assert not (tmp_path / "2026-10-06.json").exists()
    assert (tmp_path / "2026-10-07.json").exists()
    assert (tmp_path / "notes.json").exists()
