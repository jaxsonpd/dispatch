## @file stocks.py
#  @brief Briefing plugin — reports a configurable list of stock tickers in a table.
#
#  Fetches live price, daily change, market cap, and 52-week range for each
#  ticker using yfinance and renders them as a data table with an alert if any
#  ticker fails to load.
#
#  @par Configuration
#  Edit the TICKERS list below to control which symbols are reported.
#
#  @par Dependencies
#  @code
#  pip install yfinance
#  @endcode

import yfinance as yf

from dispatch import Section

# ── Configuration ─────────────────────────────────────────────────────────────

## Symbols to include in the briefing table.
TICKERS: list[str] = [
    "SPY",
    "MSFT",
    "GOOGL",
    "AMZN",
    "MCHP",
]

## Section heading shown in the briefing document.
SECTION_TITLE = "Stock Market Overview"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_price(value: float | None) -> str:
    """Format a price to two decimal places, or return '—' for missing data."""
    return f"${value:,.2f}" if value is not None else "—"


def _fmt_change(change: float | None, pct: float | None) -> str:
    """Format absolute + percentage daily change with a +/- sign prefix."""
    if change is None or pct is None:
        return "—"
    sign = "+" if change >= 0 else ""
    return f"{sign}{change:.2f} ({sign}{pct:.2f}%)"


def _fmt_market_cap(mc: float | None) -> str:
    """Abbreviate market cap into T / B / M suffix."""
    if mc is None:
        return "—"
    if mc >= 1e12:
        return f"${mc / 1e12:.2f}T"
    if mc >= 1e9:
        return f"${mc / 1e9:.2f}B"
    if mc >= 1e6:
        return f"${mc / 1e6:.2f}M"
    return f"${mc:,.0f}"


def _fmt_range(low: float | None, high: float | None) -> str:
    """Format 52-week low–high range."""
    if low is None or high is None:
        return "—"
    return f"${low:,.2f} – ${high:,.2f}"


# ── Plugin entry-point ────────────────────────────────────────────────────────

def get_section() -> Section | None:
    """
    @brief Build and return the stocks section.

    Fetches summary data for every ticker in TICKERS, renders a data table,
    and adds warning alerts for any tickers that could not be retrieved.

    @return Populated Section, or None if every ticker failed.
    """
    section = Section(SECTION_TITLE)

    rows: list[list[str]] = []
    failed: list[str] = []

    for symbol in TICKERS:
        try:
            info = yf.Ticker(symbol).fast_info

            price      = getattr(info, "last_price",          None)
            prev_close = getattr(info, "previous_close",      None)
            market_cap = getattr(info, "market_cap",          None)
            week52_low  = getattr(info, "year_low",           None)
            week52_high = getattr(info, "year_high",          None)

            # Derive daily change from last price vs previous close.
            if price is not None and prev_close is not None and prev_close != 0:
                change     = price - prev_close
                change_pct = (change / prev_close) * 100.0
            else:
                change = change_pct = None

            rows.append([
                symbol,
                _fmt_price(price),
                _fmt_change(change, change_pct),
                _fmt_market_cap(market_cap),
                _fmt_range(week52_low, week52_high),
            ])

        except Exception as exc:  # noqa: BLE001
            failed.append(symbol)
            # Log to stderr so the briefing runner can capture it if desired.
            import sys
            print(f"[stocks plugin] failed to fetch {symbol}: {exc}", file=sys.stderr)

    # Nothing to show if every ticker failed.
    if not rows:
        section.add_alert(
            "Stock data unavailable",
            "Could not retrieve data for any of the configured tickers. "
            "Check your network connection or yfinance installation.",
            "danger",
        )
        return section

    section.add_table(
        headers=["Ticker", "Last Price", "Day Change", "Market Cap", "52-Week Range"],
        rows=rows,
    )

    # Per-ticker warnings for partial failures.
    for symbol in failed:
        section.add_alert(
            f"Missing data: {symbol}",
            f"Could not retrieve information for {symbol}. "
            "It may be delisted, misspelled, or temporarily unavailable.",
            "warning",
        )

    return section
