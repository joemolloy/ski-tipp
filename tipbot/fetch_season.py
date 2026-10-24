"""Import a season's races and results from FIS into a history database for the tipbot.

Uses the app's own scrapers, so run it against a copy, never the live database:

    cp ski-tipp-db.sqlite3.backup tipbot/data/history.sqlite3
    DB_LOCATION=tipbot/data/history.sqlite3 DJANGO_SECRET_KEY=dev \
        uv run python tipbot/fetch_season.py "2025/2026" 2026 WC

FIS rate limits at roughly 40 requests, so requests are spaced out and a 429 waits and retries.
"""
import os
import sys
import time
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "istv.settings")

import django

django.setup()

from skitipp import fis_connector, race_web_scraper
from skitipp.models import RaceEvent, RACE_TYPES, Season

CALENDAR = (
    "https://www.fis-ski.com/DB/alpine-skiing/calendar-results.html?eventselection=&place="
    "&sectorcode=AL&seasoncode={code}&categorycode={category}&disciplinecode=DH,SL,GS,SG"
    "&gendercode=M&racedate=&racecodex=&nationcode=&seasonmonth=X-{code}&saveselection=-1&seasonselection="
)
PAUSE = 4


def throttled_fis_get(url, session=None, timeout=15):
    for attempt in range(6):
        time.sleep(PAUSE)
        try:
            return _fis_get(url, session=session, timeout=timeout)
        except fis_connector.FISRateLimited:
            print("rate limited, waiting 90s", file=sys.stderr)
            time.sleep(90)
    raise fis_connector.FISRateLimited(url)


_fis_get = fis_connector.fis_get
fis_connector.fis_get = throttled_fis_get
race_web_scraper.fis_get = throttled_fis_get


def main(season_name, season_code, category):
    season, _ = Season.objects.get_or_create(
        name=season_name,
        defaults=dict(
            start_date=date(season_code - 1, 10, 1),
            end_date=date(season_code, 3, 31),
            fis_calendar=CALENDAR.format(code=season_code, category=category),
        ),
    )
    events = race_web_scraper.get_season_events(season.fis_calendar, season.pk)
    race_ids = [r["fis_id"] for e in events for r in e["races"] if r["race_type"] in RACE_TYPES]
    print(f"{len(race_ids)} races in {len(events)} events")

    for race_id in race_ids:
        existing = RaceEvent.objects.filter(pk=race_id, finished=True).first()
        if existing and existing.results_published:
            continue
        try:
            race_event, _ = fis_connector.get_new_race_results(race_id, season)
        except fis_connector.FISRateLimited:
            raise
        except Exception as e:
            # old pages of cancelled races can lack a date; skip them rather than stop the season
            print(f"skipped race {race_id}: {e!r}", file=sys.stderr)
            continue
        if race_event.start_list.filter(rank=1).exists():
            race_event.finished = True
        elif race_event.race_date.date() < date.today():
            race_event.cancelled = True
        race_event.save()
        print("imported", race_event, race_event.status, file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else "WC")
