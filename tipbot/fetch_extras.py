"""Scrape extras from FIS for the tipbot: speed training runs, racer nations, venue countries.

For every finished race in the given databases it loads the race page, which gives the venue's
country and the racers' nations. For speed races it then follows the event page to the event's
training runs. Everything goes into tipbot/data/fis.sqlite3 (tables tipbot_event,
tipbot_training and tipbot_racer). Races already fetched are skipped, so it can be rerun after
each weekend:

    uv run python tipbot/fetch_extras.py tipbot/data/history.sqlite3 tipbot/data/fis.sqlite3

Training runs come out the days before a downhill, so they're known at tipping time. FIS rate
limits at roughly 40 requests, so requests are spaced out and a 429 waits and retries.
"""
import os
import re
import sqlite3
import sys
import time

from dateutil.parser import parse
from lxml import html

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "istv.settings")
os.environ.setdefault("DJANGO_SECRET_KEY", "fetch-training")

import django

django.setup()

from skitipp import fis_connector

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "fis.sqlite3")
RESULTS = "https://www.fis-ski.com/DB/general/results.html?sectorcode=AL&raceid={}"
PAUSE = 3

SCHEMA = """
create table if not exists tipbot_event (race_id integer primary key, event_id integer, country text, fetched_at text);
create table if not exists tipbot_training (
    training_id integer, event_id integer, date text, racer integer, bib integer,
    rank integer, gap real, dnf integer, primary key (training_id, racer));
create table if not exists tipbot_racer (racer integer primary key, nation text, born integer);
"""


def get(url):
    for _ in range(6):
        time.sleep(PAUSE)
        try:
            return fis_connector.fis_get(url).content
        except fis_connector.FISRateLimited:
            print("rate limited, waiting 90s", file=sys.stderr)
            time.sleep(90)
    raise fis_connector.FISRateLimited(url)


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def parse_gap(text):
    """'1:52.21' for the winner, '+0.09' for the rest; returns seconds behind."""
    text = clean(text)
    if text.startswith("+"):
        parts = text[1:].split(":")
        return sum(float(p) * 60 ** i for i, p in enumerate(reversed(parts)))
    return 0.0 if re.match(r"^\d", text) else None


def nations(content):
    """(country of the venue, [(racer, nation, born)]) from a race results page."""
    tree = html.fromstring(content, parser=html.HTMLParser(encoding="utf-8"))
    header = clean(tree.xpath('string(//div[contains(@class, "event-header__name")])'))
    country = re.search(r"\(([A-Z]{3})\)", header)
    racers = []
    for row in tree.xpath('//div[@class="g-row justify-sb"]'):
        cols = [clean(c.xpath("string(.)")) for c in row.xpath("./div")]
        if len(cols) > 5 and cols[2].isdigit():
            nation = next((c for c in cols[3:7] if re.fullmatch(r"[A-Z]{3}", c)), None)
            born = next((int(c) for c in cols[3:6] if re.fullmatch(r"(19|20)\d\d", c)), None)
            if nation:
                racers.append((int(cols[2]), nation, born))
    return (country.group(1) if country else None), racers


def training_rows(content):
    tree = html.fromstring(content, parser=html.HTMLParser(encoding="utf-8"))
    tables = tree.xpath('//div[@id="events-info-results"]')
    if not tables:
        return None, []
    date = parse(tree.xpath('string(//span[@class="date__full"])').strip()).date().isoformat()
    rows = []
    for row in tables[0].xpath('.//div[@class="g-row justify-sb"]'):
        cols = [clean(c.xpath("string(.)")) for c in row.xpath("./div")]
        if len(cols) < 6 or not cols[0].isdigit() or not cols[2].isdigit():
            continue
        rows.append(dict(rank=int(cols[0]), bib=int(cols[1]) if cols[1].isdigit() else None, racer=int(cols[2]),
                         born=int(cols[4]) if cols[4].isdigit() else None, nation=cols[5],
                         gap=parse_gap(cols[-1]), dnf=0))
    heads = tables[0].xpath('./following-sibling::div[@class="table__head"]//div[@class="g-xs-24 bold"]/text()')
    bodies = tables[0].xpath('./following-sibling::div[@class="table__body"]')
    for head, body in zip(heads, bodies):
        if "not finish" not in head.lower() and "disqualified" not in head.lower():
            continue  # did not start: nothing learned
        for row in body.xpath('.//div[@class="g-row justify-sb"]'):
            cols = [clean(c.xpath("string(.)")) for c in row.xpath("./div")]
            if len(cols) > 2 and cols[2].isdigit():
                rows.append(dict(rank=None, bib=int(cols[1]) if cols[1].isdigit() else None, racer=int(cols[2]),
                                 born=None, nation=None, gap=None, dnf=1))
    return date, rows


def event_trainings(event_url):
    tree = html.fromstring(get(event_url))
    out = []
    for row in tree.cssselect("#eventdetailscontent div.table-row div.container"):
        r = row[0][0]
        cells = [clean(a.text_content()) for a in r.findall("./a")]
        link = r.find("./a").attrib.get("href", "")
        m = re.search(r"raceid=(\d+)", link)
        if m and len(cells) > 6 and cells[5] == "TRA" and cells[6] == "M" and "Training" in cells[3]:
            out.append(int(m.group(1)))
    return out


def race_extras(race_id):
    """For predicting an upcoming race: (venue country, [(training date, row)]) from FIS."""
    content = get(RESULTS.format(race_id))
    country, _ = nations(content)
    m = re.search(r'href="([^"]*event-details[^"]*eventid=(\d+)[^"]*)"', content.decode("utf-8", "replace"))
    runs = []
    if m:
        for training_id in event_trainings(m.group(1).replace("&amp;", "&")):
            date, rows = training_rows(get(RESULTS.format(training_id)))
            runs += [(date, r) for r in rows]
    return country, runs


def main(history_dbs, out_path):
    out = sqlite3.connect(out_path, timeout=120)
    out.executescript(SCHEMA)
    done = {r for (r,) in out.execute("select race_id from tipbot_event where fetched_at is not null")}
    races = []
    for db in history_dbs:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        races += con.execute(
            "select fis_id, short_name, race_date, kind in ('Downhill', 'Super G') from skitipp_raceevent "
            "where kind in ('Downhill', 'Super G', 'Slalom', 'Giant Slalom') and finished = 1 and cancelled = 0").fetchall()
    races.sort(key=lambda r: r[2])
    events_done = {e for (e,) in out.execute("select event_id from tipbot_event where fetched_at is not null")}
    print(f"{len(races)} races, {len(done)} already fetched", file=sys.stderr)
    for race_id, name, date, speed in races:
        if race_id in done:
            continue
        content = get(RESULTS.format(race_id))
        country, racers = nations(content)
        for racer, nation, born in racers:
            out.execute("insert into tipbot_racer values (?,?,?) on conflict(racer) do update set "
                        "nation = excluded.nation, born = coalesce(excluded.born, born)", (racer, nation, born))
        page = content.decode("utf-8", "replace")
        m = re.search(r'href="([^"]*event-details[^"]*eventid=(\d+)[^"]*)"', page)
        if not m:
            print(f"{name} {date[:10]}: no event link", file=sys.stderr)
            continue
        event_url, event_id = m.group(1).replace("&amp;", "&"), int(m.group(2))
        if speed and event_id not in events_done:
            n = 0
            for training_id in event_trainings(event_url):
                tdate, rows = training_rows(get(RESULTS.format(training_id)))
                for r in rows:
                    out.execute("insert or replace into tipbot_training values (?,?,?,?,?,?,?,?)",
                                (training_id, event_id, tdate, r["racer"], r["bib"], r["rank"], r["gap"], r["dnf"]))
                n += 1 if rows else 0
            events_done.add(event_id)
            print(f"{name} {date[:10]}: event {event_id}, {n} trainings with results", file=sys.stderr)
        out.execute("insert or replace into tipbot_event values (?,?,?,datetime('now'))", (race_id, event_id, country))
        out.commit()


if __name__ == "__main__":
    main(sys.argv[1:-1] or [os.path.join(os.path.dirname(OUT), "history.sqlite3")], sys.argv[-1] if len(sys.argv) > 2 else OUT)
