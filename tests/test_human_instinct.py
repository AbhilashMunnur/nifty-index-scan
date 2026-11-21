from datetime import date

from src.desk import build_desk


def test_public_desk_no_longer_exposes_human_instinct(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    desk = build_desk(tmp_path, today=date(2026, 11, 14))
    assert "human_instinct" not in desk
    assert "master_strategy" in desk
    assert "option_buy" in desk
