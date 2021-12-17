from django.core.management.base import BaseCommand

from django.conf import settings
from django.db.models import Q
import boto3
import logging

from skitipp.models import SentReminder, RaceEvent
import datetime
from django.utils import timezone

from django.utils.timezone import get_current_timezone

class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument(
            '--dryrun',
            action='store_true',
            help="Don't send actualy messages",
        )

        parser.add_argument(
            '--resend',
            action='store_true',
            help="Send the reminder even if it was already sent before",
        )

        parser.add_argument(
            '--days',
            type=int, default=1,
            help="How many days in the future to send reminders for",
        )


    def handle(self, **options):

        dryrun=options['dryrun']
        days_into_future=options['days']
        logging.warning(f"Sending reminders for races in the next {days_into_future} days")

        client = boto3.client(
            "sns",
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_SES_REGION_NAME,
        )

        upcoming_races = (RaceEvent.objects
            .filter(race_date__gt=timezone.now())
            .filter(race_date__date__lt=datetime.date.today() + 
                datetime.timedelta(days=days_into_future)) #TODO
        )

        logging.warn(upcoming_races)

        for race in upcoming_races:
            if not options['resend'] and race.sent_reminders.exists():
                logging.warn(f'Reminders for {race} already sent>. To send them anyway, use the --resend flag')
            else:
                send_reminder_for_race(client, race, options)

def send_reminder_for_race(client, race, options):
    race_date = timezone.localtime(race.race_date)
        
    #date string
    if datetime.date.today() - race_date.date() == 1:
        race_date_str = 'Morgen'
    else:
        race_date_str = 'am ' + race_date.strftime("%b %d")

    #time string
    if race_date.time() == datetime.time(0, 0):
        race_time_str = ''
    else:
        race_time_str = ' um ' + race_date.strftime("%H:%M")

    #reminder text
    reminder_text = f"dies ist eine Erinnerung, dass {race} {race_date_str}{race_time_str} stattfindet."
    logging.warn(f'Sending "{reminder_text}" to: ')

    #send reminders, and save reminder to db
    users_to_remind =race.season.tippers.filter(tipper__isnull=False)
    
    for user in users_to_remind:
        user_reminder_text = f'Hoi {user}, ' + reminder_text 
        if user.tipper and user.tipper.mobile_number:
            logging.warn(f'\t{user} @ {user.tipper.mobile_number}')
            if not options['dryrun']:
                client.publish(
                    PhoneNumber=user.tipper.mobile_number,
                    Message=user_reminder_text
                )
                SentReminder(tipper=user, race_event=race).save()

        