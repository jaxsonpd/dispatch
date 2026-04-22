## @file briefing.py
#  @brief Core engine for the Dispatch briefing system.
#
#  Loads environment variables from .env, discovers and runs plugin modules,
#  assembles a themed PDF from their sections, and emails it to the configured inbox.
#
#  @par Usage
#  @code
#    uv run briefing          # via entry-point
#    uv run python -m dispatch.briefing
#  @endcode
#
#  @par Environment variables (.env)
#  | Variable        | Default                 | Description                        |
#  |-----------------|-------------------------|------------------------------------|
#  | SMTP_HOST       | smtp.gmail.com          | Outbound SMTP server               |
#  | SMTP_PORT       | 587                     | SMTP port (STARTTLS)               |
#  | SMTP_USER       | —                       | Sender email address               |
#  | SMTP_PASSWORD   | —                       | SMTP password / App Password       |
#  | EMAIL_TO        | —                       | Recipient email address            |
#  | PLUGINS_DIR     | src/dispatch/plugins/   | Path to plugin directory           |
#  | THEME_FILE      | theme.yaml              | Path to YAML theme file            |

import importlib
import importlib.util as _ilu
import logging
import os
import smtplib
import sys
from datetime import datetime
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT  # noqa: F401 – re-exported for plugins
from reportlab.lib.pagesizes import A4, LETTER
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

SMTP_HOST     = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
SMTP_PORT     = int(os.environ.get("SMTP_PORT") or 587)
SMTP_USER     = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
EMAIL_TO      = os.environ.get("EMAIL_TO", "")

# ── Paths ─────────────────────────────────────────────────────────────────────

_SRC_ROOT  = Path(__file__).parent
_REPO_ROOT = _SRC_ROOT.parent.parent

OUTPUT_DIR = _REPO_ROOT / "output"

# PLUGINS_DIR can be overridden via the environment variable PLUGINS_DIR.
# Relative paths are resolved from the project root; absolute paths are used as-is.
_plugins_env = os.environ.get("PLUGINS_DIR", "plugins")
print(_plugins_env)
if _plugins_env:
    _plugins_path = Path(_plugins_env)
    PLUGINS_DIR = _plugins_path if _plugins_path.is_absolute() else (_REPO_ROOT / _plugins_path)
else:
    PLUGINS_DIR = _SRC_ROOT / "plugins"

# THEME_FILE can be overridden via the environment variable THEME_FILE.
# Relative paths are resolved from the project root; absolute paths are used as-is.
_theme_env = os.environ.get("THEME_FILE", "")
if _theme_env:
    _theme_path = Path(_theme_env)
    THEME_FILE = _theme_path if _theme_path.is_absolute() else (_REPO_ROOT / _theme_path)
else:
    THEME_FILE = _SRC_ROOT / "default_theme.yaml"


# ── Theme ─────────────────────────────────────────────────────────────────────

class Theme:
    ## @brief Parsed representation of a theme.yaml file.
    #
    #  Provides typed accessors for colours, page settings, typography, and
    #  component configuration.  Falls back to sensible defaults if optional
    #  keys are absent so that a minimal theme file is still valid.
    #
    #  @par Loading
    #  @code{.py}
    #  theme = Theme.load()        # loads THEME_FILE
    #  theme = Theme.load(path)    # loads an explicit path
    #  @endcode

    _PAGE_SIZES = {"A4": A4, "LETTER": LETTER}

    def __init__(self, data: dict[str, Any]) -> None:
        ## @brief Initialise from a parsed YAML dictionary.
        #  @param data  Top-level mapping from yaml.safe_load().
        self._data    = data
        self._colours = self._resolve_colours(data.get("colours", {}))

    # ── Colour resolution ─────────────────────────────────────────────────────

    def _resolve_colours(self, raw: dict) -> dict[str, colors.Color]:
        ## @brief Convert hex strings in the colours block to ReportLab Color objects.
        #  @param raw  Raw colours dict from YAML.
        #  @return     Dict mapping colour name → Color.
        return {k: colors.HexColor(v) for k, v in raw.items()}

    def colour(self, name: str) -> colors.Color:
        ## @brief Look up a named colour from the palette.
        #
        #  @param name  Key from the @c colours block (e.g. @c "accent", @c "muted").
        #  @return      ReportLab Color object.
        #  @throws KeyError  If the colour name is not defined in the theme.
        return self._colours[name]

    # ── Convenience accessors ─────────────────────────────────────────────────

    @property
    def page_size(self) -> tuple:
        ## @brief ReportLab page size tuple (width, height) in points.
        key = self._data.get("page", {}).get("size", "A4").upper()
        return self._PAGE_SIZES.get(key, A4)

    def page_margin(self, side: str) -> float:
        ## @brief Return a page margin in points.
        #  @param side  One of @c "top", @c "bottom", @c "left", @c "right".
        val = self._data.get("page", {}).get(f"margin_{side}", 20)
        return float(val) * mm

    @property
    def header_title(self) -> str:
        ## @brief Text shown as the PDF cover title.
        return self._data.get("header", {}).get("title", "Dispatch")

    @property
    def header_rule_thickness(self) -> float:
        ## @brief Thickness of the horizontal rule beneath the header subtitle.
        return float(self._data.get("header", {}).get("rule_thickness", 1.5))

    def typo(self, element: str) -> dict:
        ## @brief Return the raw typography dict for a named element.
        #  @param element  Key from the @c typography block (e.g. @c "body", @c "title").
        return self._data.get("typography", {}).get(element, {})

    def comp(self, component: str) -> dict:
        ## @brief Return the raw component styling dict.
        #  @param component  Key from the @c components block (e.g. @c "table", @c "alert").
        return self._data.get("components", {}).get(component, {})

    # ── Style builder ─────────────────────────────────────────────────────────

    def _para_style(self, name: str, internal_name: str, **defaults) -> ParagraphStyle:
        ## @brief Build a ParagraphStyle from theme typography values with fallback defaults.
        #
        #  @param name           Key in the @c typography block.
        #  @param internal_name  ReportLab internal style name (must be unique).
        #  @param defaults       Fallback kwargs if the key is absent from the theme.
        #  @return               Configured ParagraphStyle.
        t           = self.typo(name)
        base        = getSampleStyleSheet()["Normal"]
        colour_key  = t.get("colour", defaults.get("colour", "dark"))
        text_colour = self._colours.get(colour_key, self._colours.get("dark", colors.black))

        kwargs: dict[str, Any] = {
            "fontName":  t.get("font_name",  defaults.get("font_name",  "Helvetica")),
            "fontSize":  t.get("font_size",   defaults.get("font_size",   10)),
            "textColor": text_colour,
        }
        for attr, key in [
            ("spaceAfter",  "space_after"),
            ("spaceBefore", "space_before"),
            ("leading",     "leading"),
        ]:
            val = t.get(key, defaults.get(key))
            if val is not None:
                kwargs[attr] = val

        return ParagraphStyle(internal_name, parent=base, **kwargs)

    def build_styles(self) -> dict[str, ParagraphStyle]:
        ## @brief Build and return the full paragraph style dictionary for this theme.
        #  @return  Dict mapping style key → ParagraphStyle.
        return {
            "title":          self._para_style("title",          "BriefTitle",
                                font_name="Helvetica-Bold", font_size=22,
                                colour="dark", space_after=6),
            "subtitle":       self._para_style("subtitle",       "BriefSubtitle",
                                font_name="Helvetica", font_size=10,
                                colour="muted", space_before=30, space_after=16),
            "section_header": self._para_style("section_header", "SectionHeader",
                                font_name="Helvetica-Bold", font_size=13,
                                colour="accent", space_before=14, space_after=6),
            "body":           self._para_style("body",           "Body",
                                font_name="Helvetica", font_size=9,
                                colour="dark", space_after=4, leading=14),
            "small":          self._para_style("small",          "Small",
                                font_name="Helvetica", font_size=8,
                                colour="muted", space_after=2),
            "alert_title":    self._para_style("alert_title",    "AlertTitle",
                                font_name="Helvetica-Bold", font_size=10,
                                colour="dark", space_after=2),
            "kv_label":       self._para_style("kv_label",       "KVLabel",
                                font_name="Helvetica-Bold", font_size=8,
                                colour="muted"),
            "kv_value":       self._para_style("kv_value",       "KVValue",
                                font_name="Helvetica-Bold", font_size=11,
                                colour="dark"),
        }

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def load(cls, path: Path | None = None) -> "Theme":
        ## @brief Load and parse a theme YAML file.
        #
        #  Falls back to built-in defaults if the file does not exist, so the
        #  system works out of the box without a theme file present.
        #
        #  @param path  Optional explicit path to a @c .yaml file.
        #  @return      Initialised Theme instance.
        target = path or THEME_FILE
        if not target.exists():
            log.warning("Theme file not found: %s — using built-in defaults", target)
            return cls(_DEFAULT_THEME)
        with open(target, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        log.info("Theme loaded ← %s", target)
        return cls(data)


# ── Built-in default theme ────────────────────────────────────────────────────
# Used when no theme.yaml is present. Mirrors the values in theme.yaml exactly
# so behaviour is identical whether the file exists or not.

_DEFAULT_THEME: dict[str, Any] = {
    "colours": {
        "accent":     "#1a56db",
        "dark":       "#111827",
        "muted":      "#6b7280",
        "bg_light":   "#f3f4f6",
        "success":    "#059669",
        "warning":    "#d97706",
        "danger":     "#dc2626",
        "info_bg":    "#eff6ff",
        "success_bg": "#ecfdf5",
        "warning_bg": "#fffbeb",
        "danger_bg":  "#fef2f2",
        "grid":       "#e5e7eb",
    },
    "page": {
        "size": "A4",
        "margin_top": 20, "margin_bottom": 20,
        "margin_left": 20, "margin_right": 20,
    },
    "header": {"title": "Dispatch", "rule_thickness": 1.5},
    "components": {
        "table":   {"header_font": "Helvetica-Bold", "header_size": 8,
                    "padding": 6, "header_padding": 4},
        "kv_grid": {"columns": 3, "padding": 6},
        "alert":   {"padding_left": 10, "padding_right": 10,
                    "padding_top": 8, "padding_bottom": 8, "border_width": 3},
    },
}

# ── Module-level theme instance ───────────────────────────────────────────────
# Loaded once at import time. All Section instances share this by default.

THEME = Theme.load()


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

    def __init__(self, title: str, theme: Theme | None = None) -> None:
        ## @brief Initialise a section with a display title.
        #  @param title  Text shown as the section header in the PDF.
        #  @param theme  Theme instance to use; defaults to the module-level THEME.
        self.title      = title
        self._theme     = theme or THEME
        self._styles    = self._theme.build_styles()
        self._flowables = []

    # ── Content helpers ───────────────────────────────────────────────────────

    def add_paragraph(self, text: str, style: str = "body") -> None:
        ## @brief Append a paragraph of text to the section.
        #
        #  Supports a subset of HTML inline tags: @c \<b\>, @c \<i\>, @c \<br/\>.
        #
        #  @param text   Content string, may contain HTML inline markup.
        #  @param style  Style key: @c "body" (default), @c "small",
        #               @c "alert_title", @c "kv_label", @c "kv_value".
        self._flowables.append(Paragraph(text, self._styles[style]))

    def add_key_values(self, items: list[tuple[str, str]]) -> None:
        ## @brief Render a stat grid of label / value pairs.
        #
        #  Items are laid out in a configurable-column grid (default 3).
        #  Rows are zebra-striped.  Ideal for at-a-glance numeric summaries.
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
        cols = self._theme.comp("kv_grid").get("columns", 3)
        pad  = self._theme.comp("kv_grid").get("padding", 6)

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
            ("LEFTPADDING",   (0, 0), (-1, -1), pad),
            ("RIGHTPADDING",  (0, 0), (-1, -1), pad),
            ("TOPPADDING",    (0, 0), (-1, -1), pad),
            ("BOTTOMPADDING", (0, 0), (-1, -1), pad),
            ("ROWBACKGROUNDS",(0, 0), (-1, -1), [self._theme.colour("bg_light"), colors.white]),
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
        tc         = self._theme.comp("table")
        pad        = tc.get("padding", 6)
        hpad       = tc.get("header_padding", 4)

        header_row = [Paragraph(h, s["kv_label"]) for h in headers]
        data_rows  = [[Paragraph(str(c), s["body"]) for c in row] for row in rows]

        t = Table([header_row] + data_rows, colWidths=col_widths)
        t.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1,  0), self._theme.colour("accent")),
            ("TEXTCOLOR",     (0, 0), (-1,  0), colors.white),
            ("FONTNAME",      (0, 0), (-1,  0), tc.get("header_font", "Helvetica-Bold")),
            ("FONTSIZE",      (0, 0), (-1,  0), tc.get("header_size", 8)),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, self._theme.colour("bg_light")]),
            ("GRID",          (0, 0), (-1, -1), 0.25, self._theme.colour("grid")),
            ("LEFTPADDING",   (0, 0), (-1,  0), hpad),
            ("RIGHTPADDING",  (0, 0), (-1,  0), hpad),
            ("TOPPADDING",    (0, 0), (-1,  0), hpad),
            ("BOTTOMPADDING", (0, 0), (-1,  0), hpad),
            ("LEFTPADDING",   (0, 1), (-1, -1), pad),
            ("RIGHTPADDING",  (0, 1), (-1, -1), pad),
            ("TOPPADDING",    (0, 1), (-1, -1), pad),
            ("BOTTOMPADDING", (0, 1), (-1, -1), pad),
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
            "info":    (self._theme.colour("info_bg"),    self._theme.colour("accent")),
            "success": (self._theme.colour("success_bg"), self._theme.colour("success")),
            "warning": (self._theme.colour("warning_bg"), self._theme.colour("warning")),
            "danger":  (self._theme.colour("danger_bg"),  self._theme.colour("danger")),
        }
        bg, border = colour_map.get(level, colour_map["info"])
        ac = self._theme.comp("alert")
        s  = self._styles

        t = Table(
            [[[ Paragraph(title, s["alert_title"]),
                Paragraph(body,  s["body"]) ]]],
            colWidths=[A4[0] - 40 * mm],
        )
        t.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, -1), bg),
            ("LEFTPADDING",   (0, 0), (-1, -1), ac.get("padding_left",   10)),
            ("RIGHTPADDING",  (0, 0), (-1, -1), ac.get("padding_right",  10)),
            ("TOPPADDING",    (0, 0), (-1, -1), ac.get("padding_top",     8)),
            ("BOTTOMPADDING", (0, 0), (-1, -1), ac.get("padding_bottom",  8)),
            ("LINEBEFORE",    (0, 0), ( 0, -1), ac.get("border_width",    3), border),
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
        #  @param styles  Style dictionary from Theme.build_styles().
        #  @return        Ordered list of ReportLab Flowable objects.
        rule_thickness = self._theme.typo("section_header").get("rule_thickness", 0.5)
        return [
            Paragraph(self.title, styles["section_header"]),
            HRFlowable(width="100%", thickness=rule_thickness,
                       color=self._theme.colour("accent"), spaceAfter=6),
            *self._flowables,
        ]


# ── PDF builder ───────────────────────────────────────────────────────────────

def build_pdf(sections: list[Section], theme: Theme | None = None) -> Path:
    ## @brief Assemble all sections into a dated PDF file.
    #
    #  The output directory is created if it does not exist.
    #  If a PDF for today already exists it is overwritten.
    #
    #  @param sections  Ordered list of Section objects to include.
    #  @param theme     Theme to use; defaults to the module-level THEME.
    #  @return          Path to the written PDF file.
    t = theme or THEME
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"briefing_{datetime.now():%Y-%m-%d}.pdf"

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=t.page_size,
        leftMargin=t.page_margin("left"),
        rightMargin=t.page_margin("right"),
        topMargin=t.page_margin("top"),
        bottomMargin=t.page_margin("bottom"),
    )

    styles = t.build_styles()
    now    = datetime.now()
    story  = [
        Paragraph(t.header_title, styles["title"]),
        Paragraph(f"{_fmt_date(now)}  ·  Generated {now:%H:%M}", styles["subtitle"]),
        HRFlowable(width="100%", thickness=t.header_rule_thickness,
                   color=t.colour("accent"), spaceAfter=10),
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
    #  @throws ValueError          If SMTP_USER or EMAIL_TO are not configured.
    #  @throws smtplib.SMTPException  On any SMTP-layer error.
    if not SMTP_USER or not EMAIL_TO:
        raise ValueError(
            "SMTP_USER and EMAIL_TO must be set in .env or environment variables."
        )

    date_str       = _fmt_date(datetime.now())
    msg            = MIMEMultipart()
    msg["From"]    = SMTP_USER
    msg["To"]      = EMAIL_TO
    msg["Subject"] = f"Dispatch — {date_str}"

    msg.attach(MIMEText(
        f"Good morning!\n\nYour daily briefing for {date_str} is attached.\n\n"
        "— Dispatch",
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
    ## @brief Discover and run all plugin modules from the built-in plugins directory
    #         and any additional directories specified in environment variables.
    #
    #  A valid plugin is any @c .py file that exposes a top-level @c get_section()
    #  callable.  Files beginning with @c _ are skipped (reserved for templates and
    #  @c __init__.py).  Plugins that return @c None are silently skipped (useful for
    #  conditional alerts).  Exceptions raised by individual plugins are caught and
    #  logged so that one broken plugin cannot abort the entire briefing.
    #
    #  Plugins are always loaded from the built-in @c src/dispatch/plugins/ package
    #  first, followed by any directories listed in the @c PLUGINS_DIR environment
    #  variable (colon-separated on Unix, semicolon-separated on Windows).  External
    #  directories are loaded directly from the filesystem using @c importlib.util so
    #  no special package structure is needed — just plain @c .py files with a
    #  @c get_section() function.  If a plugin filename exists in multiple directories,
    #  later directories take precedence (their section replaces the earlier one).
    #
    #  @return  List of Section objects, built-in plugins first then extra dirs in
    #           order, each group sorted alphabetically by filename.

    _builtin_plugins = _SRC_ROOT / "plugins"

    # Build the ordered list of (path, is_builtin) directories to search.
    dirs: list[tuple[Path, bool]] = []

    if _builtin_plugins.exists():
        dirs.append((_builtin_plugins, True))
    else:
        log.warning("Built-in plugins directory not found: %s", _builtin_plugins)

    if _plugins_env:
        for raw in _plugins_env.split(os.pathsep):
            p = Path(raw.strip())
            if p.exists():
                dirs.append((p, False))
            else:
                log.warning("Extra plugins directory not found (PLUGINS_DIR): %s", p)

    if not dirs:
        log.error("No plugins directories available.")
        return []

    # Ensure the package root is on sys.path for built-in import_module calls.
    sys.path.insert(0, str(_SRC_ROOT.parent))

    sections: list[Section] = []

    for plugins_dir, is_builtin in dirs:
        log.info("Loading plugins from %s", plugins_dir)

        for plugin_path in sorted(plugins_dir.glob("*.py")):
            name = plugin_path.stem
            if name.startswith("_"):
                continue

            try:
                if is_builtin:
                    module = importlib.import_module(f"dispatch.plugins.{name}")
                else:
                    spec   = _ilu.spec_from_file_location(f"_dispatch_plugin_{name}", plugin_path)
                    module = _ilu.module_from_spec(spec)
                    spec.loader.exec_module(module)
            except Exception:
                log.exception("Plugin import failed  ✗  %s  (%s)", name, plugins_dir)
                continue

            if not hasattr(module, "get_section"):
                log.debug("Skipping %s — no get_section()", name)
                continue

            try:
                section = module.get_section()
                if section is not None:
                    sections.append(section)
                    log.info("Plugin loaded  ✓  %s  (%s)", name, plugins_dir)
                else:
                    log.info("Plugin skipped    %s  (returned None)", name)
            except Exception:
                log.exception("Plugin failed  ✗  %s  (%s)", name, plugins_dir)

    return sections


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    ## @brief Main entry point — load plugins, build PDF, send email.
    log.info("=== Dispatch ===")

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