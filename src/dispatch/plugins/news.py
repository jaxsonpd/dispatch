## @file news.py
#  @brief Plugin: top headlines from an RSS feed.
#
#  Defaults to BBC News. Swap @c FEED_URL and @c FEED_LABEL for any RSS source:
#  @code
#    # RNZ:       https://www.rnz.co.nz/rss/news.xml
#    # NZ Herald: https://www.nzherald.co.nz/rss/news/
#    # BBC:       https://feeds.bbci.co.uk/news/rss.xml
#  @endcode
#
#  No API key required.

import html
import urllib.request
import xml.etree.ElementTree as ET

from dispatch import Section

## @brief URL of the RSS feed to fetch.
FEED_URL = "https://feeds.bbci.co.uk/news/rss.xml"
## @brief Human-readable source name shown in the section header.
FEED_LABEL = "BBC News"
## @brief Maximum number of headline items to include.
MAX_ITEMS = 4


def get_section() -> Section:
    ## @brief Fetch the RSS feed and return a Section of headlines.
    #
    #  Each item renders as a bold title followed by a short description.
    #  Descriptions longer than 200 characters are truncated with an ellipsis.
    #  On network or parse failure an alert is shown instead of raising.
    #
    #  @return Section containing up to MAX_ITEMS headlines, or an error alert.
    section = Section(f"Headlines — {FEED_LABEL}")

    try:
        req = urllib.request.Request(FEED_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            tree = ET.parse(resp)
    except Exception as exc:
        section.add_alert("Could not fetch news feed", str(exc), "warning")
        return section

    items = tree.findall(".//item")[:MAX_ITEMS]
    if not items:
        section.add_paragraph("No items found in feed.")
        return section

    for item in items:
        title = html.unescape(item.findtext("title") or "")
        desc  = html.unescape(item.findtext("description") or "")
        if len(desc) > 200:
            desc = desc[:197] + "…"
        section.add_paragraph(f"<b>{title}</b>", style="body")
        if desc:
            section.add_paragraph(desc, style="small")
        section.add_spacer(2)

    return section
