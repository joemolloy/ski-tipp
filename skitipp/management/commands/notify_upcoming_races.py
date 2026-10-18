from django.core.management.base import BaseCommand

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse
import logging

from skitipp.context_processors import TIPP_ASSIST_GROUP
from skitipp.models import SentReminder, RaceEvent, Tipp
import datetime
from django.utils import timezone


class Command(BaseCommand):
    help = "Email Tipp-Assist members who haven't tipped an upcoming race, or tipped a racer who isn't starting"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dryrun',
            action='store_true',
            help="Don't send actual emails",
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


    def handle(self, **options):
        now = timezone.now()
        upcoming_races = RaceEvent.objects.filter(
            cancelled=False,
            race_date__gt=now,
            race_date__lte=now + datetime.timedelta(hours=options['hours']),
        ).select_related('season')

        logging.warning(f"Checking {upcoming_races.count()} races in the next {options['hours']} hours")

        for race in upcoming_races:
            if race.season is None:
                continue
            tippers = race.season.tippers.filter(is_active=True, groups__name=TIPP_ASSIST_GROUP).select_related('tipper')
            for user in tippers:
                problem = tipp_problem(user, race)
                if not problem:
                    continue

                if SentReminder.objects.filter(tipper=user, race_event=race).exists() and not options['resend']:
                    continue

                send_reminder(user, race, problem, options['dryrun'])


def tipp_problem(user, race):
    """Why this tipper needs a reminder for this race, or None if their tipp is fine."""
    tipp = Tipp.objects.filter(
        tipper=user, race_event=race, created__lt=race.race_date
    ).order_by('-created').first()

    if tipp is None:
        return "Du hast noch keinen Tipp abgegeben."

    starters = set(race.start_list.values_list('racer_id', flat=True))
    if starters:
        not_starting = [r for r in (tipp.place_1, tipp.place_2, tipp.place_3) if r.fis_id not in starters]
        if not_starting:
            names = ', '.join(r.name for r in not_starting)
            return f"In deinem Tipp steht nicht auf der Startliste: {names}."

    return None


def send_reminder(user, race, problem, dryrun):
    race_date = timezone.localtime(race.race_date)
    tipp_url = settings.SITE_URL + reverse('create_tipp', kwargs={'race_id': race.pk})

    when = race_date.strftime("%d.%m. um %H:%M")
    text = f"Hoi {user}, {race} ({race.location}) startet am {when}. {problem} Tippen: {tipp_url}"

    auto_tipp_on = hasattr(user, 'tipper') and user.tipper.auto_tipp_since is not None
    if auto_tipp_on and problem.startswith("Du hast noch keinen"):
        text += " (Auto-Tipp ist aktiv: ohne Tipp bekommst du den Tipp der Mehrheit.)"

    logging.warning(f"{'[dryrun] ' if dryrun else ''}{user}: {text}")
    if dryrun:
        return

    if not user.email:
        return

    send_mail(
        subject=f"Ski-tipp: {race} {race.location}",
        message=text,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=True,
    )

    SentReminder(tipper=user, race_event=race).save()
