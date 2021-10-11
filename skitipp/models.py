from django.db import models
from django.db.models import OuterRef, Subquery, Q
from django.contrib.auth.models import User
from django.urls import reverse

from django.utils import timezone

import skitipp.tipp_scorer as tipp_scorer

class RacerQuerySet(models.QuerySet):
    def competitors(self):
        return self.exclude(fis_id=0)

class Racer(models.Model):
    fis_id = models.IntegerField(primary_key=True)
    name = models.CharField(max_length=100)
    active = models.BooleanField(default=True)
    rank = models.IntegerField(null=True, blank=True)

    objects = RacerQuerySet.as_manager()

    @property
    def lname(self):
        return self.name.split(' ')[0]

    def __str__(self):
        return self.name

RACE_TYPES = {
        "Slalom" : "tech",
        "Giant Slalom" : "tech",
        "Super G": "speed",
        "Downhill" : "speed",
        "Alpine combined" : "speed",
        "City Event" : "other",
        "Parallel Giant Slalom" : "other",
        "Parallel Slalom" : "other"
    }

class Season(models.Model):
    name = models.CharField(max_length=200,null=False)
    start_date = models.DateField(null=False)
    end_date = models.DateField(null=False)
    tippers = models.ManyToManyField('auth.User')
    current = models.BooleanField(default=False)
    fis_calendar = models.URLField(null=True, max_length=500)

    def __str__(self):
        return self.name

    def select_season_url(self):
        return reverse('select_season', kwargs={'pk': self.pk})

    def get_absolute_url(self):
        return reverse("race_list", kwargs={"season_id": self.pk})
    


class RaceEvent(models.Model):
    fis_id = models.IntegerField(primary_key=True)
    
    location = models.CharField(max_length=200)
    kind = models.CharField(max_length=200)
    race_date = models.DateTimeField()

    cancelled = models.BooleanField(default=False)
    in_progress = models.BooleanField(default=False)
    finished = models.BooleanField(default=False)
    points_multiplier = models.IntegerField(default=1)

    short_name = models.CharField(max_length=10)
    start_list_length = models.IntegerField(null=False)

    season = models.ForeignKey('Season',
        related_name='races', on_delete=models.CASCADE, null=True, blank=True
    )

    def get_absolute_url(self):
        return reverse('race_detail', kwargs={'pk': self.pk})

    @property
    def podium(self):
        podium = self.start_list.filter(is_dnf=False, rank__lte=3).order_by('rank')
        return podium

    @property
    def dnfs(self):
        return self.start_list.filter(is_dnf=True).order_by('racer__name')

    @property
    def alle_im_ziel(self):
        return not self.dnfs.exists()

    @property
    def status(self):
        if self.cancelled:
            return "cancelled"
        elif self.finished:
            return "finished"
        else:
            return "not_started"

    @property
    def get_last_tipps(self):
        last_tipps = []
        for u in User.objects.all().prefetch_related('tipps'):
            user_last_tipp = u.tipps.filter(
                Q(race_event=self) & Q(Q(created__lt=self.race_date) | Q(corrected_tipp=True))
            ).order_by("-created").first()

            if user_last_tipp:
                last_tipps.append(user_last_tipp)

        return last_tipps

    @property
    def is_tech_event(self):
        return RACE_TYPES[self.kind] == 'tech'

    @property
    def is_speed_event(self):
        return RACE_TYPES[self.kind] == 'speed'

    @property
    def dnf_eligible(self):
        return self.is_tech_event or self.is_speed_event

    def detail_link(self):
        return reverse('race_detail', kwargs={'pk': self.pk})
    
    def tipp_link(self):
            return reverse('create_tipp', kwargs={'race_id': self.pk})
        
    @property
    def start_date_in_past(self):
        return self.race_date < timezone.now()

    def __str__(self):
        return self.short_name

class RaceCompetitor(models.Model):

    race_event = models.ForeignKey('RaceEvent',
        related_name='start_list', on_delete=models.CASCADE
    )
    racer = models.ForeignKey('Racer',
        related_name='start_lists', on_delete=models.PROTECT
    )
    start_number = models.IntegerField()
    rank = models.IntegerField(null=True)
    is_dnf = models.BooleanField(default=False)


class Tipp(models.Model):
    tipper = models.ForeignKey('auth.User', related_name='tipps', on_delete=models.CASCADE)
    race_event = models.ForeignKey('RaceEvent', related_name='tipps', on_delete=models.CASCADE)

    created = models.DateTimeField(auto_now_add=True)

    place_1 = models.ForeignKey('Racer', related_name='place_1', on_delete=models.DO_NOTHING)
    place_2 = models.ForeignKey('Racer', related_name='place_2', on_delete=models.DO_NOTHING)
    place_3 = models.ForeignKey('Racer', related_name='place_3', on_delete=models.DO_NOTHING)

    dnf = models.ForeignKey('Racer', related_name='dnfs', on_delete=models.DO_NOTHING, null=True, blank=True)

    comment = models.TextField(null=True, blank=True)

    corrected_tipp = models.BooleanField(default=False)

    def get_absolute_url(self):
        return reverse('create_tipp', kwargs={'race_id': self.race_event.pk})

    @property
    def alle_im_ziel(self):
        return self.dnf_id == 0

    @property
    def created_on_race_day(self):
        return self.created.date() == self.race_event.race_date.date()

    @property
    def breakdown(self):
        return tipp_scorer.get_tipp_breakdown(self)

class TippPointTally(models.Model):
    tipper = models.ForeignKey('auth.User', related_name='user_points_tally', on_delete=models.CASCADE)
    race_event = models.ForeignKey('RaceEvent', related_name='race_points_tally', on_delete=models.CASCADE)
    tipp = models.OneToOneField('Tipp', related_name='tipp_points_tally', on_delete=models.CASCADE, null=True, blank=True)

    standard_points = models.FloatField(null=False, default=0)
    bonus_points = models.FloatField(null=False, default=0)

    points_multiplier = models.IntegerField(default=1)
    is_best_tipp = models.BooleanField(default=False)

    total_points = models.FloatField(null=False, default=0)

    @property
    def did_tipp(self):
        return self.tipp is not None

    def save(self, *args, **kwargs):
        #update total points
        self.total_points = self.points_multiplier * (self.standard_points + self.bonus_points)

        super().save(*args, **kwargs)  # Call the "real" save() method.

class PointAdjustment(models.Model):
    tipper = models.ForeignKey('auth.User', related_name='points_adjustments', on_delete=models.CASCADE, null=False)
    reason = models.CharField(max_length=200, null=False)
    preseason = models.BooleanField(null=False, default=False)
    created = models.DateTimeField(auto_now_add=True)
    season = models.ForeignKey('Season', related_name='points_adjustments', null=False, on_delete=models.CASCADE) 

    points = models.FloatField(null=False, help_text="(+/-)")

    def get_absolute_url(self):
        return reverse('point_adjustments', kwargs={'season_id': self.season.pk})

