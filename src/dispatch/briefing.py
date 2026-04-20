## @file briefing.py
#  @brief Core engine for the Morning Briefing system.
#
#  Loads environment variables from .env, discovers and runs plugin modules,
#  assembles a PDF from their sections, and emails it to the configured inbox.
#
#  @par Usage
#  @code
#    uv run briefing          # via entry-point
#    uv run python -m dispatch.briefing
#  @endcode
#
#  @par Environment variables (.env)
#  | Variable        | Default                        | Description                        |
#  |-----------------|--------------------------------|------------------------------------|
#  | SMTP_HOST       | smtp.gmail.com                 | Outbound SMTP server               |
#  | SMTP_PORT       | 587                            | SMTP port (STARTTLS)               |
#  | SMTP_USER       | —                              | Sender email address               |
#  | SMTP_PASSWORD   | —                              | SMTP password / App Password       |
#  | EMAIL_TO        | —                              | Recipient email address            |
#  | PLUGINS_DIR     | src/dispatch/plugins/  | Path to plugin directory           |

import importlib
import logging
import os
import pkgutil
import smtplib
import sys
from datetime import datetime
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT  # noqa: F401 – re-exported for plugins
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("briefing")

def _fmt_date(dt: datetime) -> str:
    ## @brief Format a datetime as "Monday, 5 January 2026" without a leading zero.
    #
    #  The @c %-d strftime flag (day without leading zero) is Linux/macOS only and
    #  raises ValueError on Windows.  This helper uses @c %d and strips the zero
    #  manually, giving consistent output across all platforms.
    #
    #  @param dt  datetime to format.
    #  @return    Human-readable date string, e.g. @c "Monday, 5 January 2026".
    return dt.strftime("%A, %d %B %Y").replace(" 0", " ").strip()

# ── Environment ───────────────────────────────────────────────────────────────

load_dotenv(Path(__file__).parent.parent.parent / ".env")  # project root .env

SMTP_HOST     = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT     = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER     = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
EMAIL_TO      = os.environ.get("EMAIL_TO", "")

# ── Paths ─────────────────────────────────────────────────────────────────────

_SRC_ROOT  = Path(__file__).parent
_REPO_ROOT = _SRC_ROOT.parent.parent

OUTPUT_DIR = _REPO_ROOT / "output"

# PLUGINS_DIR can be overridden via the environment variable PLUGINS_DIR.
# Relative paths are resolved from the project root; absolute paths are used as-is.
_plugins_env = os.environ.get("PLUGINS_DIR", "")
if _plugins_env:
    _plugins_path = Path(_plugins_env)
    PLUGINS_DIR = _plugins_path if _plugins_path.is_absolute() else (_REPO_ROOT / _plugins_path)
else:
    PLUGINS_DIR = _SRC_ROOT / "plugins"

# ── Colour palette ────────────────────────────────────────────────────────────

ACCENT   = colors.HexColor("#1a56db")
DARK     = colors.HexColor("#111827")
MUTED    = colors.HexColor("#6b7280")
SUCCESS  = colors.HexColor("#059669")
WARNING  = colors.HexColor("#d97706")
DANGER   = colors.HexColor("#dc2626")
BG_LIGHT = colors.HexColor("#f3f4f6")


# ── Style registry ────────────────────────────────────────────────────────────

def build_styles() -> dict[str, ParagraphStyle]:
    ## @brief Build and return the shared ReportLab paragraph style dictionary.
    #  @return dict mapping style name → ParagraphStyle
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "BriefTitle", parent=base["Normal"],
            fontSize=22, fontName="Helvetica-Bold", textColor=DARK, spaceAfter=6,
        ),
        "subtitle": ParagraphStyle(
            "BriefSubtitle", parent=base["Normal"],
            fontSize=10, fontName="Helvetica", textColor=MUTED, spaceAfter=16,
            spaceBefore=4
        ),
        "section_header": ParagraphStyle(
            "SectionHeader", parent=base["Normal"],
            fontSize=13, fontName="Helvetica-Bold",
            textColor=ACCENT, spaceBefore=14, spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "Body", parent=base["Normal"],
            fontSize=9, fontName="Helvetica",
            textColor=DARK, spaceAfter=4, leading=14,
        ),
        "small": ParagraphStyle(
            "Small", parent=base["Normal"],
            fontSize=8, fontName="Helvetica", textColor=MUTED, spaceAfter=2,
        ),
        "alert_title": ParagraphStyle(
            "AlertTitle", parent=base["Normal"],
            fontSize=10, fontName="Helvetica-Bold", textColor=DARK, spaceAfter=2,
        ),
        "kv_label": ParagraphStyle(
            "KVLabel", parent=base["Normal"],
            fontSize=8, fontName="Helvetica-Bold", textColor=MUTED,
        ),
        "kv_value": ParagraphStyle(
            "KVValue", parent=base["Normal"],
            fontSize=11, fontName="Helvetica-Bold", textColor=DARK,
        ),
    }


# ── Section ───────────────────────────────────────────────────────────────────

class Section:
    ## @brief A single named section contributed by a plugin.
    #
    #  Plugins instantiate a Section, populate it with content helpers, and
    #  return it from their @c get_section() function.  The core engine then
    #  renders all sections into one PDF in the order they were loaded.
    #
    #  @par Example
    #  @code{.py}
    #  from dispatch.briefing import Section
    #
    #  def get_section() -> Section:
    #      s = Section("My Plugin")
    #      s.add_paragraph("Hello from my plugin.")
    #      return s
    #  @endcode

    def __init__(self, title: str) -> None:
        ## @brief Initialise a section with a display title.
        #  @param title  Text shown as the section header in the PDF.
        self.title      = title
        self._flowables = []
        self._styles    = build_styles()

    # ── Content helpers ───────────────────────────────────────────────────────

    def add_paragraph(self, text: str, style: str = "body") -> None:
        ## @brief Append a paragraph of text to the section.
        #
        #  Supports a subset of HTML inline tags: @c \<b\>, @c \<i\>, @c \<br/\>.
        #
        #  @param text   Content string, may contain HTML inline markup.
        #  @param style  Style key from the style registry.
        #               Valid values: @c "body" (default), @c "small",
        #               @c "alert_title", @c "kv_label", @c "kv_value".
        self._flowables.append(Paragraph(text, self._styles[style]))

    def add_key_values(self, items: list[tuple[str, str]]) -> None:
        ## @brief Render a stat grid of label / value pairs.
        #
        #  Items are laid out in a three-column grid.  Rows are zebra-striped.
        #  Ideal for at-a-glance numeric summaries (temperature, prices, counts…).
        #
        #  @param items  List of @c (label, value) string tuples.
        #                Partial final rows are padded with empty cells.
        #
        #  @par Example
        #  @code{.py}
        #  section.add_key_values([
        #      ("Temperature", "14 °C"),
        #      ("Wind",        "32 km/h"),
        #      ("Humidity",    "78%"),
        #  ])
        #  @endcode
        s    = self._styles
        cols = 3
        # Pad to a full row
        padded = list(items) + [("", "")] * (-len(items) % cols)
        rows   = []
        for i in range(0, len(padded), cols):
            row = []
            for label, value in padded[i : i + cols]:
                row.append([
                    Paragraph(label, s["kv_label"]),
                    Paragraph(value, s["kv_value"]),
                ])
            rows.append(row)

        col_w = (A4[0] - 40 * mm) / cols
        t = Table(rows, colWidths=[col_w] * cols)
        t.setStyle(TableStyle([
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING",   (0, 0), (-1, -1), 6),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
            ("TOPPADDING",    (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("ROWBACKGROUNDS",(0, 0), (-1, -1), [BG_LIGHT, colors.white]),
        ]))
        self._flowables.append(t)
        self._flowables.append(Spacer(1, 6))

    def add_table(
        self,
        headers: list[str],
        rows: list[list],
        col_widths: list[float] | None = None,
    ) -> None:
        ## @brief Render a formatted data table with a styled header row.
        #
        #  Columns are divided equally across the page width by default.
        #  Pass explicit @p col_widths (in points) to override.
        #
        #  @param headers     List of column heading strings.
        #  @param rows        List of rows; each row is a list of cell strings.
        #  @param col_widths  Optional list of column widths in points.
        #
        #  @par Example
        #  @code{.py}
        #  section.add_table(
        #      headers=["Date", "High", "Low"],
        #      rows=[
        #          ["Mon", "15 °C", "9 °C"],
        #          ["Tue", "13 °C", "7 °C"],
        #      ],
        #  )
        #  @endcode
        s          = self._styles
        usable     = A4[0] - 40 * mm
        col_widths = col_widths or [usable / len(headers)] * len(headers)

        header_row = [Paragraph(h, s["kv_label"]) for h in headers]
        data_rows  = [[Paragraph(str(c), s["body"]) for c in row] for row in rows]

        t = Table([header_row] + data_rows, colWidths=col_widths)
        t.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1,  0), ACCENT),
            ("TEXTCOLOR",     (0, 0), (-1,  0), colors.white),
            ("FONTNAME",      (0, 0), (-1,  0), "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1,  0), 8),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, BG_LIGHT]),
            ("GRID",          (0, 0), (-1, -1), 0.25, colors.HexColor("#e5e7eb")),
            ("LEFTPADDING",   (0, 0), (-1, -1), 6),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ]))
        self._flowables.append(t)
        self._flowables.append(Spacer(1, 6))

    def add_alert(self, title: str, body: str, level: str = "info") -> None:
        ## @brief Append a coloured alert box to the section.
        #
        #  A left-border accent strip visually distinguishes alert levels.
        #
        #  @param title  Bold heading shown at the top of the alert.
        #  @param body   Descriptive text below the heading.
        #  @param level  One of @c "info" (default), @c "success",
        #               @c "warning", or @c "danger".
        #
        #  @par Example
        #  @code{.py}
        #  section.add_alert("Disk space low", "Only 2 GB remaining.", "warning")
        #  @endcode
        colour_map: dict[str, tuple] = {
            "info":    (colors.HexColor("#eff6ff"), ACCENT),
            "success": (colors.HexColor("#ecfdf5"), SUCCESS),
            "warning": (colors.HexColor("#fffbeb"), WARNING),
            "danger":  (colors.HexColor("#fef2f2"), DANGER),
        }
        bg, border = colour_map.get(level, colour_map["info"])
        s = self._styles

        t = Table(
            [[[ Paragraph(title, s["alert_title"]),
                Paragraph(body,  s["body"]) ]]],
            colWidths=[A4[0] - 40 * mm],
        )
        t.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, -1), bg),
            ("LEFTPADDING",   (0, 0), (-1, -1), 10),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 10),
            ("TOPPADDING",    (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("LINEBEFORE",    (0, 0), ( 0, -1), 3, border),
        ]))
        self._flowables.append(t)
        self._flowables.append(Spacer(1, 6))

    def add_spacer(self, height_mm: float = 4) -> None:
        ## @brief Insert vertical whitespace.
        #  @param height_mm  Height of the gap in millimetres (default 4 mm).
        self._flowables.append(Spacer(1, height_mm * mm))

    def flowables(self, styles: dict[str, ParagraphStyle]) -> list:
        ## @brief Return the complete list of ReportLab flowables for this section.
        #
        #  Called by the PDF builder; plugin authors do not need to call this.
        #
        #  @param styles  Style dictionary from build_styles().
        #  @return        Ordered list of ReportLab Flowable objects.
        return [
            Paragraph(self.title, styles["section_header"]),
            HRFlowable(width="100%", thickness=0.5, color=ACCENT, spaceAfter=6),
            *self._flowables,
        ]


# ── PDF builder ───────────────────────────────────────────────────────────────

def build_pdf(sections: list[Section]) -> Path:
    ## @brief Assemble all sections into a dated PDF file.
    #
    #  The output directory is created if it does not exist.
    #  If a PDF for today already exists it is overwritten.
    #
    #  @param sections  Ordered list of Section objects to include.
    #  @return          Path to the written PDF file.
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"briefing_{datetime.now():%Y-%m-%d}.pdf"
 
    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=20*mm, rightMargin=20*mm,
        topMargin=20*mm,  bottomMargin=20*mm,
    )
 
    styles = build_styles()
    now    = datetime.now()
    story  = [
        Paragraph("Morning Briefing", styles["title"]),
        Paragraph(f"{_fmt_date(now)}  ·  Generated {now:%H:%M}", styles["subtitle"]),
        HRFlowable(width="100%", thickness=1.5, color=ACCENT, spaceAfter=10),
    ]
 
    for section in sections:
        story.append(KeepTogether(section.flowables(styles)))
 
    doc.build(story)
    log.info("PDF written → %s", out_path)
    return out_path


# ── Email sender ──────────────────────────────────────────────────────────────

def send_email(pdf_path: Path) -> None:
    ## @brief Attach the briefing PDF to an email and send it via SMTP/STARTTLS.
    #
    #  Reads SMTP credentials from the module-level constants (populated from
    #  environment variables / .env at import time).
    #
    #  @param pdf_path  Path to the PDF file to attach.
    #  @throws ValueError  If SMTP_USER or EMAIL_TO are not configured.
    #  @throws smtplib.SMTPException  On any SMTP-layer error.
    if not SMTP_USER or not EMAIL_TO:
        raise ValueError(
            "SMTP_USER and EMAIL_TO must be set in .env or environment variables."
        )

    date_str = _fmt_date(datetime.now())
    msg          = MIMEMultipart()
    msg["From"]    = SMTP_USER
    msg["To"]      = EMAIL_TO
    msg["Subject"] = f"Morning Briefing — {date_str}"

    msg.attach(MIMEText(
        f"Good morning!\n\nYour daily briefing for {date_str} is attached.\n\n"
        "— Morning Briefing System",
        "plain",
    ))

    with open(pdf_path, "rb") as fh:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(fh.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f"attachment; filename={pdf_path.name}")
        msg.attach(part)

    log.info("Connecting to %s:%s …", SMTP_HOST, SMTP_PORT)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, EMAIL_TO, msg.as_string())

    log.info("Email sent → %s", EMAIL_TO)


# ── Plugin loader ─────────────────────────────────────────────────────────────

def load_sections() -> list[Section]:
    ## @brief Discover and run all plugin modules in the configured plugins directory.
    #
    #  A valid plugin is any @c .py file that exposes a top-level @c get_section()
    #  callable.  Files beginning with @c _ are skipped (reserved for templates and
    #  @c __init__.py).  Plugins that return @c None are silently skipped (useful for
    #  conditional alerts).  Exceptions raised by individual plugins are caught and
    #  logged so that one broken plugin cannot abort the entire briefing.
    #
    #  The directory is resolved from the @c PLUGINS_DIR environment variable, or
    #  defaults to the built-in @c src/dispatch/plugins/ package.  External
    #  directories (outside the installed package) are loaded directly from the
    #  filesystem using @c importlib.util so no special package structure is needed —
    #  just plain @c .py files with a @c get_section() function.
    #
    #  @return  List of Section objects in filename-alphabetical order.
    if not PLUGINS_DIR.exists():
        log.error("Plugins directory not found: %s", PLUGINS_DIR)
        return []

    log.info("Loading plugins from %s", PLUGINS_DIR)

    # Determine whether this is the built-in package dir or an external path.
    _builtin_plugins = _SRC_ROOT / "plugins"
    is_builtin = PLUGINS_DIR.resolve() == _builtin_plugins.resolve()

    if is_builtin:
        sys.path.insert(0, str(_SRC_ROOT.parent))

    sections: list[Section] = []
    plugin_files = sorted(PLUGINS_DIR.glob("*.py"))

    for plugin_path in plugin_files:
        name = plugin_path.stem
        if name.startswith("_"):
            continue  # skip _template, __init__, etc.

        try:
            if is_builtin:
                # Standard package import — allows relative imports within plugins.
                module = importlib.import_module(f"dispatch.plugins.{name}")
            else:
                # External file — load directly from disk.
                # Each file gets a unique module name to avoid collisions.
                import importlib.util as _ilu
                spec = _ilu.spec_from_file_location(f"_briefing_plugin_{name}", plugin_path)
                module = _ilu.module_from_spec(spec)
                spec.loader.exec_module(module)
        except Exception:
            log.exception("Plugin import failed  ✗  %s", name)
            continue

        if not hasattr(module, "get_section"):
            log.debug("Skipping %s — no get_section()", name)
            continue

        try:
            section = module.get_section()
            if section is not None:
                sections.append(section)
                log.info("Plugin loaded  ✓  %s", name)
            else:
                log.info("Plugin skipped    %s  (returned None)", name)
        except Exception:
            log.exception("Plugin failed  ✗  %s", name)

    return sections


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    ## @brief Main entry point — load plugins, build PDF, send email.
    log.info("=== Dispatcher Briefing System ===")

    sections = load_sections()
    if not sections:
        log.warning("No sections returned by any plugin — nothing to send.")
        return

    log.info("Building PDF (%d section(s))…", len(sections))
    pdf_path = build_pdf(sections)

    log.info("Sending email…")
    send_email(pdf_path)

    log.info("Done.")


if __name__ == "__main__":
    main()