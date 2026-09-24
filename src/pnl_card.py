"""Render a broker-style paper P&L card as JPG (Angel One / Kite-like)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from src.paths import ROOT
from src.strategy import favourable_move_pct, first_target_price, stop_loss_price

# Dark trading-app palette (similar to Angel One / Groww / Kite night mode).
BG = (11, 14, 17)
CARD = (22, 26, 33)
CARD_LINE = (38, 44, 54)
TEXT = (236, 239, 244)
MUTED = (148, 156, 168)
GREEN = (0, 200, 120)
RED = (255, 82, 82)
BLUE = (70, 140, 255)
AMBER = (255, 179, 71)

OUT_DIR = ROOT / "data" / "pnl_cards"


def _fonts() -> dict[str, ImageFont.FreeTypeFont | ImageFont.ImageFont]:
    # Prefer fonts that actually draw ₹ (Arial.ttf does not).
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    bold_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ]
    regular = next((p for p in candidates if Path(p).exists()), None)
    bold = next((p for p in bold_candidates if Path(p).exists()), regular)

    def load(path: str | None, size: int):
        if path:
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                pass
        return ImageFont.load_default()

    return {
        "title": load(bold, 28),
        "hero": load(bold, 44),
        "h2": load(bold, 18),
        "body": load(regular, 16),
        "small": load(regular, 13),
        "label": load(regular, 12),
        "mono": load(regular, 15),
    }


def _inr(amount: float) -> str:
    sign = "-" if amount < 0 else ""
    return f"{sign}₹{abs(amount):,.2f}"


def _pnl_color(amount: float) -> tuple[int, int, int]:
    if amount > 0:
        return GREEN
    if amount < 0:
        return RED
    return TEXT


def _rounded(draw: ImageDraw.ImageDraw, xy, radius: int, fill) -> None:
    draw.rounded_rectangle(xy, radius=radius, fill=fill)


def render_pnl_card(
    *,
    position: dict | None,
    realised_pnl: float,
    mark: float | None,
    closed: list[dict] | None = None,
    as_of: str | None = None,
    spot: float | None = None,
    rsi: float | None = None,
    out_path: Path | None = None,
    title: str = "Nifty 1H · Futures",
) -> Path:
    """Write a JPG portfolio card and return its path."""
    closed = list(closed or [])
    fonts = _fonts()
    width = 720
    height = 980 if position else 720
    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)

    unrealised = 0.0
    if position and mark and int(position.get("lots") or 0) > 0:
        entry = float(position["entry_price"])
        lots = int(position["lots"])
        lot_size = int(position.get("lot_size") or 0)
        move = mark - entry
        if position["direction"] == "SHORT":
            move = -move
        unrealised = move * lot_size * lots
    net = float(realised_pnl) + unrealised
    stamp = as_of or datetime.now().strftime("%d %b %Y %H:%M IST")

    y = 28
    draw.text((32, y), "PAPER PORTFOLIO", font=fonts["label"], fill=MUTED)
    draw.text((width - 32, y), "LIVE ORDERS OFF", font=fonts["label"], fill=AMBER, anchor="ra")
    y += 28
    draw.text((32, y), title, font=fonts["title"], fill=TEXT)
    y += 36
    draw.text((32, y), stamp, font=fonts["small"], fill=MUTED)
    y += 40

    # Hero net P&L card
    _rounded(draw, (24, y, width - 24, y + 168), 18, CARD)
    draw.text((44, y + 18), "Total P&L (MTM)", font=fonts["label"], fill=MUTED)
    draw.text((44, y + 48), _inr(net), font=fonts["hero"], fill=_pnl_color(net))
    draw.line((44, y + 108, width - 44, y + 108), fill=CARD_LINE, width=1)
    draw.text((44, y + 122), "Unrealised", font=fonts["label"], fill=MUTED)
    draw.text((44, y + 140), _inr(unrealised), font=fonts["h2"], fill=_pnl_color(unrealised))
    draw.text((width // 2 + 20, y + 122), "Realised", font=fonts["label"], fill=MUTED)
    draw.text(
        (width // 2 + 20, y + 140),
        _inr(float(realised_pnl)),
        font=fonts["h2"],
        fill=_pnl_color(float(realised_pnl)),
    )
    y += 192

    # Open position
    _rounded(draw, (24, y, width - 24, y + (300 if position else 110)), 18, CARD)
    draw.text((44, y + 16), "OPEN POSITIONS", font=fonts["label"], fill=MUTED)
    if not position or int(position.get("lots") or 0) <= 0:
        draw.text((44, y + 52), "No open position", font=fonts["body"], fill=MUTED)
        y += 130
    else:
        side = str(position["direction"])
        side_color = GREEN if side == "LONG" else RED
        symbol = str(position.get("tradingsymbol") or "FUT")
        entry = float(position["entry_price"])
        lots = int(position["lots"])
        lot_size = int(position.get("lot_size") or 0)
        qty = lots * lot_size

        draw.text((44, y + 48), symbol, font=fonts["h2"], fill=TEXT)
        # Side pill
        pill = f"  {side}  "
        bbox = draw.textbbox((0, 0), pill, font=fonts["small"])
        pw = bbox[2] - bbox[0] + 16
        ph = bbox[3] - bbox[1] + 10
        px = width - 44 - pw
        py = y + 48
        _rounded(draw, (px, py, px + pw, py + ph), 8, (32, 48, 40) if side == "LONG" else (48, 32, 32))
        draw.text((px + pw / 2, py + ph / 2), pill.strip(), font=fonts["small"], fill=side_color, anchor="mm")

        draw.text((44, y + 82), f"{lots} lot(s) · Qty {qty:,} · Lot size {lot_size}", font=fonts["small"], fill=MUTED)

        # Avg / LTP row
        draw.text((44, y + 118), "Avg price", font=fonts["label"], fill=MUTED)
        draw.text((44, y + 136), f"{entry:,.2f}", font=fonts["body"], fill=TEXT)
        draw.text((240, y + 118), "LTP", font=fonts["label"], fill=MUTED)
        ltp_text = f"{mark:,.2f}" if mark else "—"
        draw.text((240, y + 136), ltp_text, font=fonts["body"], fill=TEXT)
        draw.text((420, y + 118), "P&L", font=fonts["label"], fill=MUTED)
        draw.text((420, y + 136), _inr(unrealised), font=fonts["body"], fill=_pnl_color(unrealised))

        if mark:
            move = mark - entry
            if side == "SHORT":
                move = -move
            pct = favourable_move_pct(side, entry, mark)
            draw.text((44, y + 172), f"Change  {move:+.2f} pts  ({pct:+.2f}%)", font=fonts["small"], fill=_pnl_color(unrealised))
            notional = mark * lot_size * lots
            draw.text((44, y + 194), f"Exposure  {_inr(notional)}", font=fonts["small"], fill=MUTED)

        target = float(
            position.get("first_target_price")
            or first_target_price(side, entry, float(position.get("first_target_pct") or 1.5))
        )
        stop = float(
            position.get("stop_loss_price")
            or stop_loss_price(side, entry, float(position.get("stop_loss_pct") or 0.75))
        )
        draw.line((44, y + 220, width - 44, y + 220), fill=CARD_LINE, width=1)
        draw.text((44, y + 232), "Target 1.5%", font=fonts["label"], fill=MUTED)
        draw.text((44, y + 250), f"{target:,.2f}", font=fonts["body"], fill=GREEN)
        if mark:
            draw.text((44, y + 272), f"{target - mark:+.2f} pts", font=fonts["small"], fill=MUTED)
        draw.text((280, y + 232), "Stop 0.75%", font=fonts["label"], fill=MUTED)
        draw.text((280, y + 250), f"{stop:,.2f}", font=fonts["body"], fill=RED)
        if mark:
            draw.text((280, y + 272), f"{mark - stop:+.2f} pts cushion", font=fonts["small"], fill=MUTED)
        if rsi is not None:
            draw.text((500, y + 232), "1H RSI", font=fonts["label"], fill=MUTED)
            draw.text((500, y + 250), f"{rsi:.1f}", font=fonts["body"], fill=BLUE)
        y += 320

    # Closed trades summary
    _rounded(draw, (24, y, width - 24, y + 180), 18, CARD)
    draw.text((44, y + 16), "CLOSED TRADES", font=fonts["label"], fill=MUTED)
    if not closed:
        draw.text((44, y + 56), "No closed trades yet", font=fonts["body"], fill=MUTED)
    else:
        row_y = y + 48
        for trade in closed[-3:]:
            pnl = float(trade.get("pnl") or 0)
            line = (
                f"{trade.get('direction')} {trade.get('lots')} lot  "
                f"{float(trade.get('entry_price') or 0):,.1f} → {float(trade.get('exit_price') or 0):,.1f}"
            )
            draw.text((44, row_y), line, font=fonts["small"], fill=TEXT)
            draw.text((width - 44, row_y), _inr(pnl), font=fonts["small"], fill=_pnl_color(pnl), anchor="ra")
            reason = str(trade.get("exit_reason") or "")[:48]
            draw.text((44, row_y + 20), reason, font=fonts["label"], fill=MUTED)
            row_y += 48
        draw.line((44, y + 148, width - 44, y + 148), fill=CARD_LINE, width=1)
        draw.text((44, y + 158), f"{len(closed)} closed · realised {_inr(float(realised_pnl))}", font=fonts["small"], fill=MUTED)

    # Footer
    footer_y = height - 36
    spot_bit = f"  ·  Spot {spot:,.2f}" if spot else ""
    draw.text(
        (width // 2, footer_y),
        f"Paper book only{spot_bit}  ·  Not investment advice",
        font=fonts["label"],
        fill=MUTED,
        anchor="mm",
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = out_path or OUT_DIR / f"pnl_{datetime.now():%Y%m%d_%H%M%S}.jpg"
    img.save(path, format="JPEG", quality=92, optimize=True)
    return path
