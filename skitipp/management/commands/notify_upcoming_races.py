from django.core.management.base import BaseCommand

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse
import boto3
import logging
import unicodedata

from skitipp.context_processors import TIPP_ASSIST_GROUP
from skitipp.models import SentReminder, RaceEvent, Tipp
from skitipp.tipp_assist_views import assist_tipps
import datetime
from django.utils import timezone


class Command(BaseCommand):
    help = "Remind tippers who haven't tipped an upcoming race, or tipped a racer who isn't starting"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dryrun',
            action='store_true',
            help="Don't send actual messages",
        )

        parser.add_argument(
            '--resend',
            action='store_true',
            help="Send the reminder even if one was already sent for the race",
        )

        parser.add_argument(
            '--hours',
            type=int, default=24,
            help="Remind about races starting in the next N hours",
        )

        parser.add_argument(
            '--better-by',
            type=float, default=0.25,
            help="Text Tipp-Assist members when the best tipp beats theirs by at least this many expected points",
        )


    def handle(self, **options):
        now = timezone.now()
        upcoming_races = list(RaceEvent.objects.filter(
            cancelled=False,
            race_date__gt=now,
            race_date__lte=now + datetime.timedelta(hours=options['hours']),
        ).select_related('season'))

        logging.warning(f"Checking {len(upcoming_races)} races in the next {options['hours']} hours")

        sms_client = None
        if getattr(settings, 'AWS_ACCESS_KEY_ID', None):
            sms_client = boto3.client(
                "sns",
                aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                region_name=settings.AWS_SES_REGION_NAME,
            )

        assist_cache = {}
        for race in upcoming_races:
            if race.season is None:
                continue
            # reminders are on unless the tipper turned them off on their profile
            tippers = race.season.tippers.filter(is_active=True).exclude(tipper__reminders=False).select_related('tipper')
            for user in tippers:
                if SentReminder.objects.filter(tipper=user, race_event=race).exists() and not options['resend']:
                    continue

                problem = tipp_problem(user, race)

                suggestion = None
                if gets_sms(user):
                    if user.pk not in assist_cache:
                        assist_cache[user.pk] = assist_for(user, upcoming_races)
                    suggestion = better_tipp(assist_cache[user.pk], race, options['better_by'])

                if problem or suggestion:
                    send_reminder(sms_client, user, race, problem, suggestion, options['dryrun'])


def gets_sms(user):
    """Tipp-Assist members with a mobile number get an SMS as well as the email."""
    mobile = user.tipper.mobile_number if hasattr(user, 'tipper') else ''
    return bool(mobile) and user.groups.filter(name=TIPP_ASSIST_GROUP).exists()


def assist_for(user, races):
    # the tipbot shouldn't stop the reminders if it fails
    try:
        return assist_tipps(races, user)
    except Exception:
        logging.exception(f"Tipp-Assist failed for {user}")
        return None


def better_tipp(assist, race, better_by):
    """Tipp-Assist's best tipp, if the user hasn't tipped or it beats their tipp by better_by expected points."""
    if assist is None:
        return None
    by_id, _, label = assist
    res = by_id.get(race.fis_id)
    if not res:
        return None

    current = res.get("current")
    current_expected = current["expected"] if current else None
    if current is not None and res["expected"] - current_expected < better_by:
        return None

    #FIS writes surnames in capitals: "PINHEIRO BRAATHEN Lucas" -> "PINHEIRO BRAATHEN"
    surname = lambda fis_id: " ".join(w for w in label(fis_id).split() if w.isupper()) or label(fis_id)
    return dict(
        podium=[surname(r) for r in res["podium"]],
        dnf=surname(res["dnf"]) if res["dnf"] else None,
        expected=res["expected"], current_expected=current_expected,
    )


def tipp_problem(user, race):
    """Why this tipper needs a reminder for this race, as (email text, short SMS text), or None if their tipp is fine."""
    tipp = Tipp.objects.filter(
        tipper=user, race_event=race, created__lt=race.race_date
    ).order_by('-created').first()

    if tipp is None:
        return "Du hast noch keinen Tipp abgegeben.", "Noch kein Tipp!"

    starters = set(race.start_list.values_list('racer_id', flat=True))
    if starters:
        not_starting = [r for r in (tipp.place_1, tipp.place_2, tipp.place_3) if r.fis_id not in starters]
        if not_starting:
            names = ', '.join(r.name for r in not_starting)
            return (f"In deinem Tipp steht nicht auf der Startliste: {names}.",
                    f"{', '.join(r.lname for r in not_starting)} startet nicht!")

    return None


SMS_LENGTH = 160


def sms_text(race, problem, suggestion):
    """The reminder SMS, kept to one 160 character message where the names allow it."""
    #it goes out on race morning, so the start time is enough
    when = timezone.localtime(race.race_date).strftime("%H:%M")
    #a bare link, phones still make it clickable
    link = (settings.SITE_URL + reverse('tipp_assist' if suggestion else 'create_tipp',
                                        kwargs={} if suggestion else {'race_id': race.pk})).split("://")[-1]

    def build(shorten):
        parts = [f"{race} {when}:"]
        if problem:
            parts.append(problem[1])
        if suggestion:
            #the last word skips particles: "VON GRUENIGEN" -> "GRUENIGEN", "PINHEIRO BRAATHEN" -> "BRAATHEN"
            name = (lambda n: n.split()[-1]) if shorten else (lambda n: n)
            dnf = name(suggestion["dnf"]) if suggestion["dnf"] else "Alle im Ziel"
            tipp = f"{', '.join(name(n) for n in suggestion['podium'])}, DNF {dnf}"
            if suggestion["current_expected"] is None:
                parts.append(f"Tipp-Assist: {tipp} ({suggestion['expected']:.1f} Pkt)")
            else:
                parts.append(f"Tipp-Assist besser: {tipp} ({suggestion['expected']:.1f} statt {suggestion['current_expected']:.1f} Pkt)")
        parts.append(link)
        return gsm_safe(" ".join(parts))

    text = build(shorten=False)
    return text if len(text) <= SMS_LENGTH else build(shorten=True)


def publish_sms(sms_client, number, text):
    # transactional SMS are time critical and get through carrier filters more reliably than the promotional default
    return sms_client.publish(
        PhoneNumber=number, Message=text,
        MessageAttributes={'AWS.SNS.SMS.SMSType': {'DataType': 'String', 'StringValue': 'Transactional'}},
    )


def gsm_safe(text):
    # one character outside the GSM alphabet makes the whole SMS unicode, which fits only 70 characters
    return unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode()


def sms_parts(text):
    return 1 if len(text) <= SMS_LENGTH else -(-len(text) // 153)


def send_reminder(sms_client, user, race, problem, suggestion, dryrun):
    race_date = timezone.localtime(race.race_date)
    tipp_url = settings.SITE_URL + reverse('create_tipp', kwargs={'race_id': race.pk})

    when = race_date.strftime("%d.%m. um %H:%M")
    intro = f"Hoi {user}, {race} ({race.location}) startet am {when}."

    #everyone gets an email about a missing or broken tipp
    email_text = None
    if problem and user.email:
        email_text = f"{intro} {problem[0]} Tippen: {tipp_url}"

    #Tipp-Assist members with a mobile number also get an SMS, with Tipp-Assist's tipp when it's better
    sms = sms_text(race, problem, suggestion) if gets_sms(user) and (problem or suggestion) else None

    prefix = '[dryrun] ' if dryrun else ''
    if email_text:
        logging.warning(f"{prefix}{user} (email): {email_text}")
    if sms:
        logging.warning(f"{prefix}{user} (sms, {len(sms)} chars, {sms_parts(sms)} part): {sms}")
    if dryrun or not (email_text or sms):
        return

    if email_text:
        send_mail(
            subject=f"Ski-tipp: {race} {race.location}",
            message=email_text,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=True,
        )

    if sms and sms_client:
        publish_sms(sms_client, user.tipper.mobile_number, sms)

    SentReminder(tipper=user, race_event=race).save()
