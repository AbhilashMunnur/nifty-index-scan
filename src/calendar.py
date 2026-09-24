"""NSE trading-day helpers. Weekends and NSE holidays do not count."""

from __future__ import annotations

from datetime import date

import numpy as np

# Weekday NSE holidays for 2026 (weekend festivals omitted — they are not trading days anyway).
NSE_HOLIDAYS = np.array(
    [
        "2026-01-15",  # Municipal Corporation Election — Maharashtra
        "2026-01-26",  # Republic Day
        "2026-03-03",  # Holi
        "2026-03-26",  # Shri Ram Navami
        "2026-03-31",  # Shri Mahavir Jayanti
        "2026-04-03",  # Good Friday
        "2026-04-14",  # Dr. Baba Saheb Ambedkar Jayanti
        "2026-05-01",  # Maharashtra Day
        "2026-05-28",  # Bakri Id
        "2026-06-26",  # Muharram
        "2026-09-14",  # Ganesh Chaturthi
        "2026-10-02",  # Mahatma Gandhi Jayanti
        "2026-10-20",  # Dussehra
        "2026-11-10",  # Diwali-Balipratipada
        "2026-11-24",  # Guru Nanak Jayanti
        "2026-12-25",  # Christmas
    ],
    dtype="datetime64[D]",
)


def trading_days_remaining(as_of: date, expiry: date) -> int:
    """Trading sessions from today through expiry, inclusive.

    Entering on expiry day counts as 0 remaining days after the close, so
    expiry itself is included only while it is still a session you can hold.
    """
    if expiry < as_of:
        return 0
    start = np.datetime64(as_of, "D")
    end = np.datetime64(expiry, "D") + np.timedelta64(1, "D")
    return int(np.busday_count(start, end, holidays=NSE_HOLIDAYS))


def is_nse_trading_day(as_of: date) -> bool:
    return bool(np.is_busday(np.datetime64(as_of, "D"), holidays=NSE_HOLIDAYS))
