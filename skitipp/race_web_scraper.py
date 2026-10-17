from lxml import html, etree, cssselect
import requests
import re
from skitipp.models import RaceEvent
from skitipp.fis_connector import fis_get, FISRateLimited

def get_season_events(calendar_url, season_id):    
    if not calendar_url:
        return []

    print('loading events from ' +  calendar_url)
    existing_race_ids = RaceEvent.objects.filter(season=season_id).values_list('pk', flat=True)

    session = requests.Session()

    page = fis_get(calendar_url, session=session)
    tree = html.fromstring(page.content)

    calendar_rows = tree.xpath('//div[@id="calendardata"]//div[@class="g-row"]')
    events = []

    #print(calendar_rows)

    for event_row in calendar_rows:
        # Identify event URL from calendar row first (anchor with class bold)
        bold_links = event_row.cssselect('a.bold')
        if not bold_links:
            continue
        event_url = bold_links[0].attrib.get('href', '').strip()

        # the link text is the sector and genders, e.g. "AL W M"; skip women-only events
        # to stay under the FIS rate limit
        genders = re.sub(r'\s+', ' ', bold_links[0].text_content()).strip().split(' ')
        if genders[0] == 'AL' and 'M' not in genders[1:]:
            continue

        if event_url:
            try:
                event_page = fis_get(event_url, session=session, timeout=10)
                event_tree = html.fromstring(event_page.content, parser=html.HTMLParser(encoding='utf-8'))
                header_name = event_tree.cssselect('h1.event-header__name')
                if header_name:
                   place_text = header_name[0].text_content().strip()

                # Fallback to the calendar row if place not found from event page
                else:
                    place_text = row_place(event_row)

                event = dict(
                    event_place = place_text,
                    event_url = event_url
                )

                print(event['event_place'], event['event_url'])
                event['races'] = get_races_for_event(existing_race_ids, event_page.content)
                if event['races']:
                    events.append(event)
            except Exception as e:
                print('Failed to load event page for place name:', event_url, e)
                rate_limited = isinstance(e, FISRateLimited)
                events.append(dict(event_place=row_place(event_row), event_url=event_url, races=[], error=str(e), rate_limited=rate_limited))
                if rate_limited:
                    break

    return events


def row_place(event_row):
    # place name as shown in the calendar row, used when the event page can't be loaded
    places = event_row.cssselect('span.bold')
    return re.sub(r'\s+', ' ', places[0].text_content()).strip() if places else '?'


def get_races_for_event(existing_race_ids, page_content):
    tree2 = html.fromstring(page_content)
    race_rows = tree2.cssselect("#eventdetailscontent div.table-row div.container")
    print('rr', race_rows)
    races = []
    for race_row in race_rows:
        race_row = race_row[0][0]

        def clean(txt):
            if txt is None:
                return ''
            # Collapse all whitespace (including newlines/tabs) to single spaces and strip ends
            return re.sub(r'\s+', ' ', txt).strip()

        race = dict(
            link = race_row.find('./a').attrib['href'],
            race_date = clean(race_row.find('./a[2]').text_content()),
            race_type = clean(race_row.find('./a[4]').text_content()),
            race_category = clean(race_row.find('./a[6]').text_content()),
            race_gender = clean(race_row.find('./a[7]').text_content()) 
        )
        race['fis_id'] = int(re.search(r'raceid=(\d+)', race['link']).group(1))
        race['exists'] = race['fis_id'] in existing_race_ids

        print(race['race_date'], race['race_category'], race['race_gender'])
        print()

        if race['race_category']  in ['WSC', 'WC', 'OWG', 'WM'] and race['race_gender'] == 'M':
            races.append(race)    
    return races


if __name__ == "__main__":
    test_url = 'https://www.fis-ski.com/DB/alpine-skiing/calendar-results.html?eventselection=&place=&sectorcode=AL&seasoncode=2022&categorycode=WC&disciplinecode=&gendercode=M&racedate=&racecodex=&nationcode=&seasonmonth=X-2022&saveselection=-1&seasonselection='
    get_season_events(test_url, 4)

#print(race_name)

