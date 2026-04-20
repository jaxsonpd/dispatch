## @file _template.py
#  @brief Plugin template — copy this file to create a new briefing section.
#
#  @par How to create a plugin
#  -# Copy this file to a new name inside the @c plugins/ directory,
#     e.g. @c my_stocks.py.  Files beginning with @c _ are ignored by the loader.
#  -# Implement get_section() — fetch your data, populate a Section, return it.
#  -# Drop the file in place; it is discovered automatically on the next run.
#
#  @par Section content helpers
#  | Method | Description |
#  |--------|-------------|
#  | add_paragraph(text, style) | Plain/HTML inline text |
#  | add_key_values([(label, value)]) | 3-column stat grid |
#  | add_table(headers, rows) | Full data table |
#  | add_alert(title, body, level) | Coloured alert box |
#  | add_spacer(height_mm) | Vertical whitespace |
#
#  @par Alert levels
#  @c "info" | @c "success" | @c "warning" | @c "danger"

from dispatch import Section


def get_section() -> Section | None:
    ## @brief Build and return the section for this plugin.
    #
    #  Fetch external data, handle errors gracefully (use add_alert for
    #  non-fatal failures rather than raising), and return a populated Section.
    #  Return @c None to silently skip this plugin for today's briefing.
    #
    #  @return Populated Section, or None to skip.
    section = Section("My Custom Section")

    # ── Stat grid ─────────────────────────────────────────────────────────────
    section.add_key_values([
        ("Metric A", "42"),
        ("Metric B", "7.3%"),
        ("Metric C", "Up"),
    ])

    # ── Text paragraph (supports <b> and <i> tags) ────────────────────────────
    section.add_paragraph(
        "This is a regular paragraph. "
        "You can use <b>bold</b> and <i>italic</i> HTML inline tags."
    )

    # ── Data table ────────────────────────────────────────────────────────────
    section.add_table(
        headers=["Name",   "Value",  "Change"],
        rows=[
            ["Item 1", "100.00", "+2.3%"],
            ["Item 2",  "55.40", "-0.8%"],
            ["Item 3", "210.10", "+0.1%"],
        ],
    )

    # ── Alerts ────────────────────────────────────────────────────────────────
    section.add_alert("All systems nominal", "Everything looks fine.", "success")
    section.add_alert("Watch this", "Something may need your attention.", "warning")

    # Uncomment to skip this plugin today:
    # return None

    return section
