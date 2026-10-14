import json
from datetime import date

from src.desk import build_desk


def test_desk_lists_newest_first_and_drops_tokens(tmp_path):
    history = tmp_path / "data" / "scan_history"
    history.mkdir(parents=True)
    (history / "2026-10-06.json").write_text(
        json.dumps(
            {
                "nifty_oi": [
                    _oi("2026-10-06 14:30:01", "2026-10-06 14:30", 100),
                    _oi("2026-10-06 15:10:01", "2026-10-06 15:10", 200),
                ],
                "nifty_fut": [
                    _fut("2026-10-06 09:30:01", "2026-10-06 09:30", 100.0, 6500),
                    _fut("2026-10-06 09:45:01", "2026-10-06 09:45", 120.0, 7150),
                ],
            }
        ),
        encoding="utf-8",
    )
    book = {
        "updated_at": "2026-10-06 15:40:00",
        "realised_pnl": -10.5,
        "position": {"token": "999", "lots": 0},
        "closed": [],
    }
    (tmp_path / "data" / "nifty_paper_book.json").write_text(json.dumps(book), encoding="utf-8")

    desk = build_desk(tmp_path, today=date(2026, 10, 6))
    encoded = json.dumps(desk)
    assert "999" not in encoded
    assert '"token"' not in encoded
    assert desk["generated_at"].endswith(" IST")

    assert [card["clock"] for card in desk["nifty_oi"]] == ["15:10", "14:30"]
    latest = desk["nifty_oi"][0]
    assert latest["since_1430"]["totals"].startswith("Since 14:30")
    assert "BUY" in latest["since_1430"]["strikes"][0]["call"]["label"]
    assert desk["nifty_oi"][1]["baseline"] is True

    latest_fut = desk["nifty_fut"][0]
    assert latest_fut["clock"] == "09:45"
    assert latest_fut["label"] == "NEW BUYING"
    assert latest_fut["lots"] == "10 lots"
    assert desk["nifty_fut"][1]["label"] == "BASELINE"
    assert desk["positions"]["nifty"]["flat"] is True
    assert desk["positions"]["sensex"]["saved"] is False
    assert desk["fut_days"] == []


def test_futures_day_matches_the_telegram_and_stays_for_two_days(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "nifty_fut_day_open.json").write_text(
        json.dumps(_session_mark("2026-10-14 09:33:12", 22726.50, 285686 * 65)),
        encoding="utf-8",
    )
    (data / "nifty_fut_flow_mark.json").write_text(
        json.dumps(_session_mark("2026-10-14 15:33:20", 22658.90, 287622 * 65)),
        encoding="utf-8",
    )
    (data / "sensex_fut_day_open.json").write_text(
        json.dumps(_session_mark("2026-10-14 09:33:13", 73082.55, 4967 * 20, "SENSEX26OCTFUT", "2026-10-29", 20)),
        encoding="utf-8",
    )
    (data / "sensex_fut_flow_mark.json").write_text(
        json.dumps(_session_mark("2026-10-14 15:33:20", 72959.00, 5028 * 20, "SENSEX26OCTFUT", "2026-10-29", 20)),
        encoding="utf-8",
    )

    desk = build_desk(tmp_path, today=date(2026, 10, 14))
    nifty, sensex = desk["fut_days"]
    assert nifty["name"] == "NIFTY"
    assert nifty["label"] == "SHORT BUILDUP"
    assert "NIFTY FUT DAY" in nifty["lines"][0]
    assert "285,686 → 287,622" in nifty["lines"][3]
    assert "+0.68%" in nifty["lines"][3]
    assert "22,726.50 → 22,658.90" in nifty["lines"][4]
    assert sensex["label"] == "SHORT BUILDUP"
    assert "4,967 → 5,028" in sensex["lines"][3]
    assert "token" not in json.dumps(desk["fut_days"])

    from src.desk import save_session

    save_session(tmp_path, nifty)
    save_session(tmp_path, sensex)
    (data / "nifty_fut_day_open.json").write_text(
        json.dumps(_session_mark("2026-10-15 09:33:00", 22800.0, 1000)),
        encoding="utf-8",
    )
    next_day = build_desk(tmp_path, today=date(2026, 10, 15))
    assert [card["day"] for card in next_day["fut_days"]] == ["2026-10-14", "2026-10-14"]
    assert build_desk(tmp_path, today=date(2026, 10, 16))["fut_days"] == []


def test_scans_older_than_two_days_are_removed_and_the_book_stays(tmp_path):
    history = tmp_path / "data" / "scan_history"
    history.mkdir(parents=True)
    for day, call_oi in (("2026-10-06", 100), ("2026-10-07", 110), ("2026-10-08", 120)):
        (history / f"{day}.json").write_text(
            json.dumps(
                {
                    "nifty_oi": [_oi(f"{day} 15:40:01", f"{day} 15:40", call_oi)],
                    "sensex_oi": [_oi(f"{day} 15:40:01", f"{day} 15:40", call_oi)],
                    "nifty_fut": [_fut(f"{day} 15:15:01", f"{day} 15:15", 100.0, 6500)],
                    "sensex_fut": [_fut(f"{day} 15:15:01", f"{day} 15:15", 100.0, 200)],
                }
            ),
            encoding="utf-8",
        )
    (tmp_path / "data" / "nifty_paper_book.json").write_text(
        json.dumps({"updated_at": "2026-09-11 09:42:08", "realised_pnl": -1, "position": None, "closed": []}),
        encoding="utf-8",
    )

    desk = build_desk(tmp_path, today=date(2026, 10, 8))

    assert not (history / "2026-10-06.json").exists()
    assert (history / "2026-10-07.json").exists()
    assert (history / "2026-10-08.json").exists()
    for key in ("nifty_oi", "sensex_oi", "nifty_fut", "sensex_fut"):
        assert [card["day"] for card in desk[key]] == ["2026-10-08", "2026-10-07"]
    assert desk["positions"]["nifty"]["saved"] is True
    assert desk["positions"]["nifty"]["realised"] == -1.0


def _oi(updated: str, slot: str, call_oi: int) -> dict:
    return {
        "updated_at": updated,
        "slot": slot,
        "expiry": "2026-10-06",
        "atm": 22700,
        "lot_size": 65,
        "strikes": [
            {
                "label": "ATM",
                "strike": 22700,
                "call_oi": call_oi * 65,
                "put_oi": 65,
                "call_ltp": 10.0 if call_oi == 100 else 14.0,
                "put_ltp": 8.0,
            }
        ],
    }


def _session_mark(updated, price, oi, symbol="NIFTY27OCT26FUT", expiry="2026-10-27", lot_size=65) -> dict:
    return {
        "updated_at": updated,
        "tradingsymbol": symbol,
        "token": "secret-instrument",
        "expiry": expiry,
        "lot_size": lot_size,
        "price": price,
        "oi": oi,
    }


def _fut(updated: str, slot: str, price: float, oi: int) -> dict:
    return {
        "updated_at": updated,
        "slot": slot,
        "tradingsymbol": "NIFTY27OCT26FUT",
        "token": "secret-instrument",
        "expiry": "2026-10-27",
        "lot_size": 65,
        "price": price,
        "oi": oi,
    }
