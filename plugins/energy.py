## @file energy.py
#  @brief Briefing plugin — reports WTI, Brent, and TTF futures prices.
#
#  Shows the front-month (M0) and second-month (M1) futures contracts for each
#  benchmark, with daily change and the M0/M1 spread.
#
#  @par Contract symbol format (yfinance)
#  Explicit delivery-month symbols are constructed as ROOT + MONTH_CODE + YY
#  plus an exchange suffix, e.g. CLK25.NYM (WTI May-25).
#
#  | Benchmark | Root | Exchange suffix | Month codes        |
#  |-----------|------|-----------------|--------------------|
#  | WTI       | CL   | .NYM            | all 12             |
#  | Brent     | BZ   | .NYM            | all 12             |
#  | TTF       | TTF  | .NYM            | all 12             |
#
#  M0 is the current calendar month; M1 is the next.  If the front month has
#  already expired (no last_price returned) the plugin automatically advances
#  by one month so you always see live contracts.
#
#  @par Dependencies
#  @code
#  pip install yfinance
#  @endcode

from __future__ import annotations

import sys
import datetime
from typing import NamedTuple

import yfinance as yf

from dispatch import Section

# ── Configuration ─────────────────────────────────────────────────────────────

SECTION_TITLE = "Energy Futures"

## CME/ICE futures month codes (Jan–Dec).
_MONTH_CODES = "FGHJKMNQUVXZ"


class Benchmark(NamedTuple):
    label:    str   ## Display name, e.g. "WTI Crude"
    unit:     str   ## Price unit, e.g. "USD/bbl"
    root:     str   ## Contract root, e.g. "CL"
    suffix:   str   ## Exchange suffix, e.g. ".NYM"


BENCHMARKS: list[Benchmark] = [
    Benchmark("WTI Crude",   "USD/bbl", "CL",  ".NYM"),
    Benchmark("Brent Crude", "USD/bbl", "BZ",  ".NYM"),
    Benchmark("TTF Gas",     "EUR/MWh", "TTF", ".NYM"),
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _contract_symbol(root: str, suffix: str, year: int, month: int) -> str:
    """Build a yfinance explicit futures symbol, e.g. CLK25.NYM."""
    return f"{root}{_MONTH_CODES[month - 1]}{str(year)[-2:]}{suffix}"


def _front_two(root: str, suffix: str) -> tuple[str, str]:
    """
    Return (M0_symbol, M1_symbol) starting from the current month.

    If M0 has no last_price (already expired/rolled), advance by one month
    so we always land on live contracts.
    """
    today = datetime.date.today()
    year, month = today.year, today.month

    for _ in range(3):  # try up to 3 months forward to find a live M0
        sym = _contract_symbol(root, suffix, year, month)
        info = yf.Ticker(sym).fast_info
        if getattr(info, "last_price", None) is not None:
            # Found a live M0 — compute M1
            m1_month = month % 12 + 1
            m1_year  = year + (1 if month == 12 else 0)
            m1_sym   = _contract_symbol(root, suffix, m1_year, m1_month)
            return sym, m1_sym
        # Advance one month
        month = month % 12 + 1
        if month == 1:
            year += 1

    # Fallback: return current-month symbols even if stale
    sym = _contract_symbol(root, suffix, today.year, today.month)
    m1_month = today.month % 12 + 1
    m1_year  = today.year + (1 if today.month == 12 else 0)
    return sym, _contract_symbol(root, suffix, m1_year, m1_month)


def _fetch(symbol: str) -> tuple[float | None, float | None, float | None]:
    """Return (last_price, day_change, day_change_pct) for *symbol*."""
    info       = yf.Ticker(symbol).fast_info
    price      = getattr(info, "last_price",     None)
    prev_close = getattr(info, "previous_close", None)
    if price is not None and prev_close is not None and prev_close != 0:
        change     = price - prev_close
        change_pct = (change / prev_close) * 100.0
    else:
        change = change_pct = None
    return price, change, change_pct


def _fmt_price(value: float | None, unit: str) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f} {unit}"


def _fmt_change(change: float | None, pct: float | None) -> str:
    if change is None or pct is None:
        return "—"
    sign = "+" if change >= 0 else ""
    return f"{sign}{change:.2f} ({sign}{pct:.2f}%)"


def _fmt_spread(m0: float | None, m1: float | None) -> str:
    """M1 − M0: positive = contango, negative = backwardation."""
    if m0 is None or m1 is None:
        return "—"
    spread = m1 - m0
    sign  = "+" if spread >= 0 else ""
    label = "contango" if spread >= 0 else "backwardation"
    return f"{sign}{spread:.2f} ({label})"


# ── Plugin entry-point ────────────────────────────────────────────────────────

def get_section() -> Section | None:
    """
    @brief Build and return the energy futures section.

    Constructs explicit delivery-month contract symbols for M0 and M1,
    fetches price and daily change, and renders a comparison table with the
    M0/M1 spread.

    @return Populated Section, or None if every benchmark failed.
    """
    section = Section(SECTION_TITLE)

    rows:   list[list[str]] = []
    failed: list[str]       = []

    for bm in BENCHMARKS:
        try:
            m0_sym, m1_sym = _front_two(bm.root, bm.suffix)

            m0_price, m0_chg, m0_chg_pct = _fetch(m0_sym)
            m1_price, m1_chg, m1_chg_pct = _fetch(m1_sym)

            rows.append([
                bm.label,
                m0_sym,
                _fmt_price(m0_price, bm.unit),
                _fmt_change(m0_chg, m0_chg_pct),
                m1_sym,
                _fmt_price(m1_price, bm.unit),
                _fmt_change(m1_chg, m1_chg_pct),
                _fmt_spread(m0_price, m1_price),
            ])

        except Exception as exc:  # noqa: BLE001
            failed.append(bm.label)
            print(f"[energy plugin] failed to fetch {bm.label}: {exc}", file=sys.stderr)

    if not rows:
        section.add_alert(
            "Energy data unavailable",
            "Could not retrieve data for any benchmark. "
            "Check your network connection or yfinance installation.",
            "danger",
        )
        return section

    section.add_table(
        headers=[
            "Benchmark",
            "M0 Contract", "M0 Price",  "M0 Day Chg",
            "M1 Contract", "M1 Price",  "M1 Day Chg",
            "Spread (M1−M0)",
        ],
        rows=rows,
    )

    section.add_paragraph(
        "<b>Contango</b>: M1 &gt; M0 (storage premium). "
        "<b>Backwardation</b>: M1 &lt; M0 (supply tightness)."
    )

    for label in failed:
        section.add_alert(
            f"Missing data: {label}",
            f"Could not retrieve information for {label}. "
            "The exchange may be closed or the symbol temporarily unavailable.",
            "warning",
        )

    return section