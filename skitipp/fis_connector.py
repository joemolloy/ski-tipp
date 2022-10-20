from lxml import html, etree
import requests
from datetime import datetime
from dateutil.parser import parse
import dateutil.parser as dp
import re

from django.shortcuts import get_object_or_404


from skitipp.models import RaceEvent, Racer, RaceCompetitor

def create_short_name(race_name, race_kind):
    abrv_name = race_name[:4].strip()

    race_kind_mapping = {
        "Slalom" : "SL",
        "Giant Slalom" : "GS",
        "Downhill" : "DH",
        "Super G": "SG",
        "Alpine combined" : "AC",
        "City Event" : "CE",
        "Parallel Giant Slalom" : "PGS",
        "Parallel Slalom" : "PSL"
    }
    abrv_kind = race_kind_mapping.get(race_kind, "??")

    return abrv_name.strip() + "_" + abrv_kind

def extract_race_info(tree, fis_race_id, season=None):

    race_name = tree.xpath('//div[@class="event-header__name heading_off-sm-style"]//h1/text()')[0]

    race_kind = tree.xpath('//div[@class="event-header__kind"]/text()')[0]
    race_kind =     race_kind.strip().split(' ', 1)[1]    #remove "Men's " from race_kind

    race_date = tree.xpath('//span[@class="date__full"]/text()')[0]

    race_time_elem = tree.xpath('//div[@class="time schedule-list__time"]')
    
    if race_time_elem:
        race_time = race_time_elem[0].attrib['data-default-time']
        race_time_zone = race_time_elem[0].attrib['data-default-timezone']
        date_time_string = '{} {} {}'.format(race_date, race_time, race_time_zone)
    else:
        date_time_string = race_date


    race_date_time = parse(date_time_string)
    race_short_name = create_short_name(race_name, race_kind)

    print(date_time_string)
    print(race_short_name, race_name, race_kind, race_date_time)

    (race_event, created) = RaceEvent.objects.get_or_create(fis_id=fis_race_id, defaults={
        "location":race_name, "kind":race_kind, "race_date":race_date_time, 'start_list_length':30,
        "season": season
    })
    if not race_event.finished:
        race_event.race_date = race_date_time #only update race time if race isn't finished to allow editing of date

    race_event.save()
    
    if created:
        race_event.short_name = race_short_name
        race_event.save()

    return (race_event, created)

def get_results_html_table(tree):
    return tree.xpath('//div[@id="events-info-results"]')[0]

def update_ws_start_list():
    fis_base_link = 'https://www.fis-ski.com/DB/alpine-skiing/cup-standings.html?sectorcode=AL&seasoncode=2021&cupcode=WCSL&disciplinecode=ALL&gendercode=M&nationcode='
    p = re.compile("competitorid=(\d+)")

    page = requests.get(fis_base_link)
    tree = html.fromstring(page.content)

    racer_links = tree.xpath('//div[@id="cupstandingsdata"]//a/@href')
    racer_names = [str(s).strip() for s in tree.xpath('//div[@id="cupstandingsdata"]//a/div[1]/div[1]/text()')]

    #set all racers to inactive
    Racer.objects.update(active=False, rank=None)

    for i, link in enumerate(racer_links):
        racer_page = requests.get(link)
        racer_tree = html.fromstring(racer_page.content)
        fis_id = int(racer_tree.xpath('//li[@id="FIS Code"]/span[@class="profile-info__value"]/text()')[0])
        racer_name = racer_names[i]
        print (i, fis_id, racer_name)

        #add new racers and set to active
        obj, created = Racer.objects.update_or_create(
            fis_id=fis_id, defaults=dict(name=racer_name, active=True, rank=i),
        )


def get_finishers(tree, race_event):
    #completed racers
    results_table = get_results_html_table(tree)
    results_rows = results_table.xpath('.//div[@class="g-row justify-sb"]')

    athletes = []
    for row in results_rows:
        cols = row.xpath('.//div/text()[normalize-space()]')
        #print("num cols: ", len(cols))
        rank = int(cols[0])
        start_number = int(cols[1])
        fis_id = int(cols[2])
        name = str(cols[3]).strip()

        print(rank, start_number, fis_id, name)

        racer, created = Racer.objects.get_or_create(fis_id=fis_id, defaults=dict(name=name))

        if racer.name != name:
            racer.name = name
            racer.save()
        
        RaceCompetitor.objects.update_or_create(
            race_event_id=race_event.fis_id, 
            racer_id=racer.fis_id, 
            defaults={'start_number': start_number, 'rank': rank}
        )

        #print(rank, bib, racer_id, name)
        athletes.append({"rank": rank, "start_number": start_number, "racer": racer})

    return athletes

def get_dnf_racers(tree, race_event):
    #dnf racers
    results_table = get_results_html_table(tree)
    dnf_headers = results_table.xpath('./following-sibling::div[@class="table__head"]//div[@class="g-xs-24 bold"]/text()')
    print(dnf_headers)
    dnf_tables = results_table.xpath('./following-sibling::div[@class="table__body"]')
    dnf_tables = zip(dnf_headers, dnf_tables)
    
    dnf_athletes = []

    for header, table in dnf_tables:
        if (header != 'Did not qualify') and ('Did not start' not in header) and ('qualification race' not in header):
            print (header)
            athlete_row = table.xpath('.//div[@class="g-row justify-sb"]') 
            for row in athlete_row:

                cols = row.xpath('.//div/text()[normalize-space()]')
                start_number = int(cols[0])
                fis_id = int(cols[1])
                name = str(cols[2]).strip()
                racer, created = Racer.objects.get_or_create(fis_id=fis_id, defaults=dict(name=name))

                if racer.name != name:
                    racer.name = name
                    racer.save()

                #if start_number <= race_event.start_list_length:
                RaceCompetitor.objects.update_or_create(
                    race_event_id=race_event.fis_id, 
                    racer_id=racer.fis_id, 
                    defaults={'start_number': start_number, 'is_dnf': True}
                )
                
                dnf_athletes.append({"start_number": start_number, "racer": racer})


        #print(bib, racer_id, name)

    #print("Number of dnf racers: ", len(dnf_athletes))
    return dnf_athletes

def results_published(tree):
    table_header = tree.xpath('.//div[@id="ajx_results"]//h3')[0].text
    return table_header in ['OFFICIAL RESULTS' , 'UNOFFICIAL RESULTS', 'UNOFFICIAL RESULTS (PARTIAL)']

def get_new_race_results(fis_race_id, season):

    fis_base_link = 'https://www.fis-ski.com/DB/general/results.html?sectorcode=AL&raceid={}'
    
    page = requests.get(fis_base_link.format(fis_race_id))
    tree = html.fromstring(page.content)

    (race_event, created) = extract_race_info(tree, fis_race_id, season)

    #delete previous competitors from race
    RaceCompetitor.objects.filter(race_event=race_event).delete()

    if results_published(tree):
        finishers = get_finishers(tree, race_event)
        dnfs = get_dnf_racers(tree, race_event)

        print("Number of completed racers: ", len(finishers))
        print("Number of dnf racers: ", len(dnfs))
    else:
        print("Official results not yet published")

    return (race_event, created)

def get_race_results(fis_race_id):

    race_event = get_object_or_404(RaceEvent, fis_id=fis_race_id)
    race_event, created = get_new_race_results(race_event.fis_id, race_event.season)

    return race_event

if __name__ == '__main__':
    get_race_results(95527, 1)