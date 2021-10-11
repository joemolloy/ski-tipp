from lxml import html, etree
import requests
import re
from skitipp.models import RaceEvent


def get_season_events(calendar_url, season_id):    
    if not calendar_url:
        return []
        
    print('loading events from ' +  calendar_url)
    existing_race_ids = RaceEvent.objects.filter(season=season_id).values_list('pk', flat=True)
    print(existing_race_ids)

    page = requests.get(calendar_url)
    tree = html.fromstring(page.content)

    calendar_rows = tree.xpath('//div[@id="calendardata"]//div[@class="g-row"]')
    events = []

    for event_row in calendar_rows:
        event_name_col = event_row.find('./a[4]')
        event = dict(
            event_place = event_name_col.text,
            event_url = event_name_col.attrib['href']
        )
        event['races'] = get_races_for_event(event['event_url'], existing_race_ids)
        events.append(event)

    return events


def get_races_for_event(event_url, existing_race_ids):
    event_page = requests.get(event_url)
    tree2 = html.fromstring(event_page.content)
    race_rows = tree2.xpath('//div[@id="eventdetailscontent"]//div[contains(@class, "table-row")]')

    races = []
    
    for race_row in race_rows:

        race = dict(
            link = race_row.find('.//a').attrib['href'],
            race_date = race_row.find('.//a[2]').text_content().strip(),
            race_type = race_row.find('.//a[4]').text_content().strip(),
            race_category = race_row.find('.//a[6]').text_content().strip(),
            race_gender = race_row.find('.//a[7]').text_content().strip() 
        )
        race['fis_id'] = int(re.search(r'raceid=(\d+)', race['link']).group(1))
        race['exists'] = race['fis_id'] in existing_race_ids

        if race['race_category']  == 'WC' and race['race_gender'] == 'M':
            races.append(race)    
        print(race)
    return races


if __name__ == "__main__":
    test_url = 'https://www.fis-ski.com/DB/alpine-skiing/calendar-results.html?eventselection=&place=&sectorcode=AL&seasoncode=2022&categorycode=WC&disciplinecode=&gendercode=M&racedate=&racecodex=&nationcode=&seasonmonth=X-2022&saveselection=-1&seasonselection='
    get_season_events(test_url, 4)

#print(race_name)

