from django.db import models
from django.db.models import OuterRef, Subquery
from django.contrib.auth.models import User
from django.urls import reverse

class RacerQuerySet(models.QuerySet):
    def competitors(self):
        return self.exclude(fis_id=0)

class Racer(models.Model):
    fis_id = models.IntegerField(primary_key=True)
    name = models.CharField(max_length=100)

    objects = RacerQuerySet.as_manager()

    def __str__(self):
        return self.name

RACE_TYPES = [
    ('SL', 'Slalom'),
    ('GS', 'Riesentorlauf'),
    ('SG', 'Super-G'),
    ('DH', 'Abfahrt'),
    ('AC', 'Kombi'),
    ('CT', 'City'),
]

class RaceEvent(models.Model):
    fis_id = models.IntegerField(primary_key=True)
    
    location = models.CharField(max_length=50)
    kind = models.CharField(max_length=20) 
    race_date = models.DateTimeField()

    cancelled = models.BooleanField(default=False)
    finished = models.BooleanField(default=False)
    points_multiplier = models.IntegerField(default=1)

    def get_absolute_url(self):
        return reverse('race_detail', kwargs={'pk': self.pk})

    @property
    def podium(self):
        podium = self.start_list.filter(is_dnf=False).order_by('rank')[:3]
        return podium

    @property
    def dnfs(self):
        return self.start_list.filter(is_dnf=True).order_by('racer__name')

    @property
    def alle_im_ziel(self):
        return not self.dnfs.exists()

    @property
    def dnf_eligible(self):
        return True

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
        for u in User.objects.filter():
            user_last_tipp = u.tipps.filter(race_event=self).order_by("-created").first()
            if user_last_tipp:
                last_tipps.append(user_last_tipp)

        return last_tipps        


    def detail_link(self):
        return reverse('race_detail', kwargs={'pk': self.pk})
    
    def tipp_link(self):
            return reverse('create_tipp', kwargs={'race_id': self.pk})
        

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

    def get_absolute_url(self):
        return reverse('create_tipp', kwargs={'race_id': self.race_event.pk})

    @property
    def alle_im_ziel(self):
        return self.dnf_id == 0
