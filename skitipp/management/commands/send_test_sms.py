from django.core.management.base import BaseCommand, CommandError
from django.conf import settings

import boto3
from botocore.exceptions import BotoCoreError, ClientError


class Command(BaseCommand):
    help = "Send a test SMS through AWS SNS and show the account's SMS settings"

    def add_arguments(self, parser):
        parser.add_argument('number', help="Phone number in international format, e.g. +41791234567")
        parser.add_argument('--message', default="Ski-tipp test SMS. If you can read this, SMS reminders work.")

    def handle(self, number, message, **options):
        if not number.startswith('+'):
            raise CommandError("Use international format, e.g. +41791234567")
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
            response = sns.publish(PhoneNumber=number, Message=message)
        except (BotoCoreError, ClientError) as e:
            raise CommandError(f"Sending failed: {e}")

        self.stdout.write(self.style.SUCCESS(
            f"Accepted by AWS, message id {response['MessageId']}. "
            "Check the phone; if nothing arrives, look at the SMS delivery logs in the SNS console."
        ))
