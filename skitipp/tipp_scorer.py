from django.contrib.auth.models import User
from django.db.models import Count, Q

from . import models as models

def score_race(race_event):
    last_tipps = race_event.get_last_tipps

    user_tallies = [score_tipp(t) for t in last_tipps]
    
    if user_tallies:
        best_score = max([ut.total_points for ut in user_tallies])
        best_tippers = [ut for ut in user_tallies if ut.total_points == best_score]

        #allinerseiger
        if len(best_tippers) == 1:
            alleine_points = 2 if race_event.is_classic else 1
            print ("Best tipper was {} (+{})".format(best_tippers[0].tipper, alleine_points))
            best_tippers[0].bonus_points += alleine_points
            best_tippers[0].is_best_tipp = True
            best_tippers[0].save()

    apply_missed_tipp_penalties(race_event, user_tallies)

def apply_missed_tipp_penalties(race_event, user_tallies):
    tippers = [rt.tipper.id for rt in user_tallies]

    #get the count of missed races for each user who didnt tip, before to the current race (race_event)
    non_tippers = User.objects.exclude(id__in=tippers).annotate(
        prev_no_tipp_offences=Count("user_points_tally", filter=Q(
                user_points_tally__race_event__season=race_event.season,
                user_points_tally__race_event__race_date__lt=race_event.race_date,
                user_points_tally__tipp__isnull=True
            )
        )
    )

    #assign negative points for missed tip
    for u in non_tippers:
        user_tally = models.TippPointTally(tipper=u, race_event=race_event, tipp=None)
        #no_tipp_penalty = int(u.prev_no_tipp_offences >= 1) 
        no_tipp_penalty = 0 #from 2020/2021 no more penalty for missed tipps.
        user_tally.standard_points = -no_tipp_penalty
        user_tally.save()

def score_tipp(tipp):
    race_event = tipp.race_event
    tipper = tipp.tipper

    print("scoring race {} for {}".format(race_event, tipper))

    standard_points, bonus_points, details = racer_points(tipp)

    user_tally = models.TippPointTally(tipper=tipper, race_event=race_event, tipp=tipp)

    #race multiplier
    user_tally.points_multiplier = race_event.points_multiplier
    print ("{} race multip: {}".format(tipper, user_tally.points_multiplier))

    user_tally.standard_points = standard_points
    user_tally.bonus_points = bonus_points

    user_tally.save()
    return user_tally

def determine_start_group(race_event, start_number):
    group = 0
    multipliers = [0,1,3,6,12]

    if race_event.is_tech_event:
        if start_number <= 7:
            group = 1
        elif start_number <= 15:
            group = 2
        elif start_number <= 30:
            group = 3
        else:
            group = 4

    elif race_event.is_speed_event:
        if start_number >= 6 and start_number <= 15:
            group = 1
        elif start_number <= 5 or (start_number >= 16 and start_number <= 20):
            group = 2
        elif start_number <= 30:
            group = 3
        else:
            group = 4

    else:
        group = 1
    
    return group, multipliers[group]

def podium_points(racer, position, race_event):
    points = 0
    tipp_on_podium = False
    correct_rank = False
    start_group_multiplier = 1
    start_number = 0
    rank = 0

    racer_start = race_event.podium.filter(racer=racer).first()
    if racer_start:
        start_number = racer_start.start_number
        rank = racer_start.rank
        start_group, start_group_multiplier = determine_start_group(race_event, start_number)

        if racer_start.rank <= 3:
            points = 1 * start_group_multiplier
            tipp_on_podium = True
        
        if racer_start.rank == position:
            correct_rank = True

        print ("{} ({}) - p{}: {}x, {}, {}".format(racer, start_number, position, start_group_multiplier, points, rank))

    return start_number, start_group_multiplier, points, tipp_on_podium, rank, correct_rank

def dnf_points(tipp, race_event):
    points = 0
    if race_event.dnf_eligible:
        if tipp.alle_im_ziel and race_event.alle_im_ziel:
            points += 0.5
        elif race_event.dnfs.filter(racer=tipp.dnf).exists():
            points += 1

    return points

def ranking_bonus_points(correct1, correct2, correct3):
    return int(correct1) + int(correct2) * 0.5 + int(correct3) * 0.5

def podium_bonus_points(tipp_on_podium1, tipp_on_podium2, tipp_on_podium3):
    number_correct = int(tipp_on_podium1) + int(tipp_on_podium2) + int(tipp_on_podium3)

    if number_correct == 2:
        return 0.5
    elif number_correct == 3:
        return 1
    else:
        return 0

def racer_points(tipp):

    race_event = tipp.race_event

    bib1, mul1, points1, tipp_on_podium1, rank1, correct1 = podium_points(tipp.place_1, 1, race_event)
    bib2, mul2, points2, tipp_on_podium2, rank2, correct2 = podium_points(tipp.place_2, 2, race_event)
    bib3, mul3, points3, tipp_on_podium3, rank3, correct3 = podium_points(tipp.place_3, 3, race_event)

    standard_points = points1 + points2 + points3

    ranking_bonus = ranking_bonus_points(correct1, correct2, correct3) #correct ranking 
    podium_bonus = podium_bonus_points(tipp_on_podium1, tipp_on_podium2, tipp_on_podium3) #bonus points for multiple correct
    dnf_bonus = dnf_points(tipp, race_event) #dnf bonus points
    bonus_points = ranking_bonus + podium_bonus + dnf_bonus

    details = dict(
        racers = [
            dict(id=1, name=tipp.place_1.lname, 
                bib=bib1 if bib1 else '-', 
                rank=rank1 if rank1 else '-', 
                mul=mul1, pod_p=points1, rang_correct=correct1),
            dict(id=2, name=tipp.place_2.lname, 
                bib=bib2 if bib2 else '-', 
                rank=rank2 if rank2 else '-', 
                mul=mul2, pod_p=points2, rang_correct=correct2),
            dict(id=3, name=tipp.place_3.lname, 
                bib=bib3 if bib3 else '-', 
                rank=rank3 if rank3 else '-', 
                mul=mul3, pod_p=points3, rang_correct=correct3),
        ],
        bonus  = dict(podium_bonus=podium_bonus, ranking_bonus=ranking_bonus, dnf_bonus=dnf_bonus)
    )

    return standard_points, bonus_points, details


def get_tipp_breakdown(tipp):
    standard_points, bonus_points, details = racer_points(tipp)

    return details