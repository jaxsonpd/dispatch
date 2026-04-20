## @file weather.py
#  @brief Plugin: current conditions and 3-day forecast for Wellington, NZ.
#
#  Data source: Open-Meteo (https://open-meteo.com) — free, no API key required.
#  Swap @c LAT / @c LON / @c CITY to report on any location.

import json
import urllib.request

from dispatch.briefing import Section

## @brief Latitude of the target location.
LAT = -41.2865
## @brief Longitude of the target location.
LON = 174.7762
## @brief Human-readable location name shown in the section header.
CITY = "Wellington, NZ"

## @brief WMO weather interpretation codes → plain-English description.
#  @see https://open-meteo.com/en/docs#weathervariables
WMO_CODES: dict[int, str] = {
    0:  "Clear sky",
    1:  "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog",          48: "Icy fog",
    51: "Light drizzle",53: "Drizzle",       55: "Heavy drizzle",
    61: "Light rain",   63: "Rain",          65: "Heavy rain",
    71: "Light snow",   73: "Snow",          75: "Heavy snow",
    80: "Showers",      81: "Heavy showers", 82: "Violent showers",
    95: "Thunderstorm", 99: "Thunderstorm with hail",
}

_API_URL = (
    "https://api.open-meteo.com/v1/forecast"
    f"?latitude={LAT}&longitude={LON}"
    "&current=temperature_2m,apparent_temperature,weathercode,"
    "windspeed_10m,precipitation,relative_humidity_2m"
    "&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,weathercode"
    "&timezone=Pacific%2FAuckland&forecast_days=3"
)


def get_section() -> Section:
    ## @brief Fetch weather data and return a populated Section.
    #
    #  On network failure an alert is shown instead of raising so that the
    #  rest of the briefing can still be assembled.
    #
    #  @return Section containing current conditions, a 3-day forecast table,
    #          and a rain alert when precipitation is detected.
    section = Section(f"Weather — {CITY}")

    try:
        with urllib.request.urlopen(_API_URL, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        section.add_alert("Could not fetch weather data", str(exc), "warning")
        return section

    cur   = data["current"]
    daily = data["daily"]

    condition = WMO_CODES.get(cur["weathercode"], "Unknown")
    section.add_key_values([
        ("Condition",    condition),
        ("Temperature",  f"{cur['temperature_2m']:.0f} °C"),
        ("Feels like",   f"{cur['apparent_temperature']:.0f} °C"),
        ("Wind",         f"{cur['windspeed_10m']:.0f} km/h"),
        ("Humidity",     f"{cur['relative_humidity_2m']:.0f}%"),
        ("Precipitation",f"{cur['precipitation']:.1f} mm"),
    ])

    section.add_table(
        headers=["Date", "Condition", "Min", "Max", "Rain"],
        rows=[
            [
                daily["time"][i],
                WMO_CODES.get(daily["weathercode"][i], "?"),
                f"{daily['temperature_2m_min'][i]:.0f} °C",
                f"{daily['temperature_2m_max'][i]:.0f} °C",
                f"{daily['precipitation_sum'][i]:.1f} mm",
            ]
            for i in range(min(3, len(daily["time"])))
        ],
    )

    if cur["precipitation"] > 0:
        section.add_alert(
            "Rain detected",
            f"{cur['precipitation']:.1f} mm of precipitation currently — bring a coat!",
            "info",
        )

    return section
