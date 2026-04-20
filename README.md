# Morning Briefing

A modular daily briefing system that assembles a PDF from plugin modules and
emails it to your inbox each morning.

## Project structure

```
morning-briefing/
├── pyproject.toml               # uv project manifest + entry point
├── uv.lock                      # locked dependency graph
├── .env.example                 # copy → .env and fill in credentials
├── .gitignore
├── main.py                      # convenience entry point
├── output/                      # generated PDFs (git-ignored)
└── src/morning_briefing/
    ├── __init__.py
    ├── briefing.py              # core engine: PDF builder + email sender
    └── plugins/
        ├── __init__.py
        ├── _template.py         # copy this to create new plugins
        ├── weather.py           # Open-Meteo weather for Wellington
        └── news.py              # BBC News RSS headlines
```

## Setup

### 1. Prerequisites

Install [uv](https://docs.astral.sh/uv/getting-started/installation/):

```bash
curl -Ls https://astral.sh/uv/install.sh | sh
```

### 2. Install dependencies

```bash
uv sync
```

### 3. Configure credentials

```bash
cp .env.example .env
$EDITOR .env          # fill in SMTP_USER, SMTP_PASSWORD, EMAIL_TO
```

**Gmail users:** generate an App Password at
<https://myaccount.google.com/apppasswords> (requires 2FA).

### 4. Run

```bash
uv run briefing       # via pyproject.toml entry point (preferred)
# or
uv run python main.py
```

A dated PDF is written to `output/` and emailed to `EMAIL_TO`.

## Scheduling

### cron

```bash
crontab -e
```

```cron
0 7 * * * cd /path/to/morning-briefing && uv run briefing >> /tmp/briefing.log 2>&1
```

### NixOS systemd user timer (home-manager)

```nix
systemd.user.services.morning-briefing = {
  Unit.Description = "Morning briefing emailer";
  Service = {
    Type = "oneshot";
    WorkingDirectory = "/path/to/morning-briefing";
    ExecStart = "${pkgs.uv}/bin/uv run briefing";
  };
};
systemd.user.timers.morning-briefing = {
  Unit.Description = "Run morning briefing at 7 am";
  Timer = { OnCalendar = "*-*-* 07:00:00"; Persistent = true; };
  Install.WantedBy = [ "timers.target" ];
};
```

## Writing a plugin

1. Copy `src/morning_briefing/plugins/_template.py` to a new file in the same
   directory (e.g. `my_stocks.py`). Files starting with `_` are skipped.
2. Implement `get_section() -> Section | None`.
3. Return `None` to silently skip the plugin on any given day.

### Section API

| Method | Description |
|--------|-------------|
| `add_paragraph(text, style="body")` | Text; supports `<b>`, `<i>` |
| `add_key_values([(label, value)])` | 3-col stat grid |
| `add_table(headers, rows, col_widths=None)` | Data table |
| `add_alert(title, body, level="info")` | Coloured alert box |
| `add_spacer(height_mm=4)` | Vertical whitespace |

Alert levels: `"info"` · `"success"` · `"warning"` · `"danger"`

### Import

```python
from morning_briefing.briefing import Section
# or shorter:
from morning_briefing import Section
```

## Plugin ideas

- **Stocks / crypto** — Yahoo Finance or CoinGecko
- **Calendar** — Google Calendar API (today's events)
- **Aviation** — METAR/TAF for NZWN from aviationweather.gov
- **Satellite** — next Sentinel-2 acquisition over Wellington
- **System health** — disk usage, uptime, backup status from your NixOS box
- **Metlink** — next bus/train departures for your route
- **RTL-SDR** — scheduled interesting passes (ISS, NOAA weather sats)