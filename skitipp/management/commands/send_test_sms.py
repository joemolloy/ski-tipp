from django.core.management.base import BaseCommand, CommandError
from django.conf import settings

import re

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from django.contrib.auth.models import User

from skitipp.models import RaceEvent, Tipper
from tipbot import markov_tipper
from skitipp.management.commands.notify_upcoming_races import (
    better_tipp, publish_sms, sms_parts, sms_text, tipp_problem)
from skitipp.tipp_assist_views import assist_tipps


def reminder_message(race_id, username):
    """The reminder SMS for this race and user, always with Tipp-Assist's tipp so the longest form gets tested."""
    if not username:
        raise CommandError("--race needs --user, whose tipp the reminder is about")
    try:
        race = RaceEvent.objects.get(pk=race_id)
        user = User.objects.get(username=username)
    except (RaceEvent.DoesNotExist, User.DoesNotExist) as e:
        raise CommandError(str(e))
    if race.start_date_in_past:
        raise CommandError(f"{race} has already started, Tipp-Assist only tipps upcoming races")
    # unlike the reminders, let Tipp-Assist errors through so the traceback shows what's wrong
    assist = assist_tipps([race], user)
    suggestion = better_tipp(assist, race, better_by=float('-inf'))
    if suggestion is None:
        raise CommandError(f"Tipp-Assist returned no tipp for {race} ({race.kind}). "
                           f"It only covers {', '.join(markov_tipper.DISCIPLINES)}.")
    return sms_text(race, tipp_problem(user, race), suggestion)


def account_number(username):
    if not username:
        raise CommandError("Give a phone number, or --user to send to that user's mobile number")
    tipper = Tipper.objects.filter(user__username=username).first()
    if not tipper or not tipper.mobile_number:
        raise CommandError(f"{username} has no mobile number. Add it in Admin -> Users")
    return tipper.mobile_number


class Command(BaseCommand):
    help = "Send a test SMS through AWS SNS and show the account's SMS settings"

    def add_arguments(self, parser):
        parser.add_argument('number', nargs='?', help="Phone number in international format, +41 and digits. Defaults to --user's mobile number")
        parser.add_argument('--message', default="Ski-tipp test SMS. If you can read this, SMS reminders work.")
        parser.add_argument('--race', type=int, help="Send the race-morning reminder for this race (FIS id) instead")
        parser.add_argument('--user', help="Send to this user's mobile number, and with --race, remind about their tipp")
        parser.add_argument('--dryrun', action='store_true', help="Print the message without sending it")

    def handle(self, number, message, **options):
        if not number:
            number = account_number(options['user'])
        if not re.fullmatch(r"\+\d{8,15}", number):
            raise CommandError(f"{number} isn't a phone number in international format: + and the country code, then digits only")

        if options['race']:
            message = reminder_message(options['race'], options['user'])
        self.stdout.write(f"To: {number}")
        self.stdout.write(f"Message ({len(message)} chars, {sms_parts(message)} SMS): {message}")
        if options['dryrun']:
            return
        if not getattr(settings, 'AWS_ACCESS_KEY_ID', None):
            raise CommandError("AWS_ACCESS_KEY_ID isn't set. Load the server's .env first: set -a; . ./.env; set +a")

        sns = boto3.client(
            "sns",
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_SES_REGION_NAME,
        )

        # these need extra permissions, so only report them if we can read them
        try:
            in_sandbox = sns.get_sms_sandbox_account_status()['IsInSandbox']
            self.stdout.write(f"SMS sandbox: {'yes, only verified numbers can receive' if in_sandbox else 'no'}")
        except (BotoCoreError, ClientError) as e:
            self.stdout.write(f"SMS sandbox: couldn't check ({e})")
        try:
            attributes = sns.get_sms_attributes(attributes=['MonthlySpendLimit', 'DefaultSMSType'])['attributes']
            self.stdout.write(f"SMS settings: {attributes or 'defaults (spend limit $1)'}")
        except (BotoCoreError, ClientError) as e:
            self.stdout.write(f"SMS settings: couldn't check ({e})")

        try:
            response = publish_sms(sns, number, message)
        except (BotoCoreError, ClientError) as e:
            raise CommandError(f"Sending failed: {e}")

        self.stdout.write(self.style.SUCCESS(
            f"Accepted by AWS, message id {response['MessageId']}. "
            "Check the phone; if nothing arrives, look at the SMS delivery logs in the SNS console."
        ))
