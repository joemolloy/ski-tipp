"""Fetch hourly race-day weather for every venue from Open-Meteo's free historical archive.

Stores it in tipbot/data/fis.sqlite3 (table tipbot_weather), one request per venue and winter:

    uv run python tipbot/fetch_weather.py tipbot/data/history.sqlite3 tipbot/data/fis.sqlite3

Coordinates are the race slopes, elevations roughly mid-course, which Open-Meteo uses to
correct the temperature. The archive lags a few days behind, so very recent races may be missing.
"""
import json
import sqlite3
import sys
import time
import urllib.request

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from markov_tipper import venue_key

# venue -> (latitude, longitude, elevation in m)
VENUES = {
    "adelboden": (46.49, 7.56, 1600), "alta": (46.55, 11.88, 1650), "are": (63.40, 13.08, 900),
    "aspen": (39.18, -106.82, 2900), "bansko": (41.84, 23.49, 1500), "beaver": (39.60, -106.52, 3000),
    "beijing": (40.52, 115.80, 1800), "bormio": (46.47, 10.37, 1700), "chamonix": (45.93, 6.87, 1300),
    "copper": (39.50, -106.15, 3000), "cortina": (46.54, 12.14, 1900), "courchevel": (45.41, 6.63, 1700),
    "crans": (46.31, 7.48, 1800), "flachau": (47.34, 13.39, 1100), "garmisch": (47.48, 11.06, 1100),
    "gurgl": (46.87, 11.03, 2100), "hafjell": (61.23, 10.45, 700), "hinterstoder": (47.70, 14.15, 1300),
    "kitzbuehel": (47.44, 12.39, 1200), "kranjska": (46.49, 13.79, 950), "kvitfjell": (61.47, 10.13, 700),
    "lake": (51.44, -116.16, 2100), "lenzerheide": (46.73, 9.56, 1700), "levi": (67.80, 24.81, 400),
    "lillehammer": (61.23, 10.45, 700), "livigno": (46.54, 10.14, 2000), "madonna": (46.23, 10.83, 1650),
    "niigata": (36.85, 138.78, 1000), "palisades": (39.20, -120.24, 2200), "pyeongchang": (37.50, 128.60, 900),
    "saalbach": (47.39, 12.64, 1400), "santacaterina": (46.41, 10.50, 1900), "schladming": (47.39, 13.69, 900),
    "soelden": (46.94, 10.90, 2800), "soldeu": (42.58, 1.67, 2200), "st": (46.49, 9.84, 2000),
    "sun": (43.69, -114.36, 2200), "valdisere": (45.45, 6.98, 2000), "valgardena": (46.56, 11.73, 1700),
    "wengen": (46.60, 7.92, 1700), "zagreb": (45.91, 15.96, 600),
}
HOURLY = ["temperature_2m", "snowfall", "precipitation", "cloud_cover", "wind_speed_10m", "shortwave_radiation"]
URL = ("https://archive-api.open-meteo.com/v1/archive?latitude={}&longitude={}&elevation={}"
       "&start_date={}&end_date={}&hourly=" + ",".join(HOURLY) + "&timezone=UTC")


def main(history_dbs, out_path):
    dates = {}  # (venue, winter) -> (first, last race date)
    for db in history_dbs:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        for location, date in con.execute("select location, race_date from skitipp_raceevent where finished = 1"):
            v = venue_key(location)
            if v:
                winter = int(date[:4]) - (int(date[5:7]) < 7)
                lo, hi = dates.get((v, winter), (date[:10], date[:10]))
                dates[(v, winter)] = (min(lo, date[:10]), max(hi, date[:10]))
    out = sqlite3.connect(out_path, timeout=120)
    out.execute("create table if not exists tipbot_weather (venue text, time text, temperature real, snowfall real, "
                "precipitation real, cloud_cover real, wind real, radiation real, primary key (venue, time))")
    done = set(out.execute("select distinct venue, substr(time, 1, 10) from tipbot_weather"))
    for (venue, winter), (lo, hi) in sorted(dates.items()):
        if venue not in VENUES:
            print(f"no coordinates for {venue}", file=sys.stderr)
            continue
        if (venue, lo) in done and (venue, hi) in done:
            continue
        lat, lon, ele = VENUES[venue]
        for attempt in range(4):
            try:
                with urllib.request.urlopen(URL.format(lat, lon, ele, lo, hi), timeout=60) as resp:
                    h = json.load(resp)["hourly"]
                break
            except Exception as e:
                print(f"{venue} {winter}: {e!r}, retrying", file=sys.stderr)
                time.sleep(5)
        else:
            continue
        rows = list(zip([venue] * len(h["time"]), h["time"], *(h[k] for k in HOURLY)))
        out.executemany("insert or replace into tipbot_weather values (?,?,?,?,?,?,?,?)", rows)
        out.commit()
        print(f"{venue}: {lo} to {hi}, {len(rows)} hours", file=sys.stderr)
        time.sleep(0.5)


if __name__ == "__main__":
    main(sys.argv[1:-1], sys.argv[-1])
