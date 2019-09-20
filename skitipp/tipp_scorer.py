from skitipp.models import RaceEvent, Racer, RaceCompetitor, Tipp, TippPointTally

def score_race(race_event):
    last_tipps = race_event.get_last_tipps

    user_tallies = [score_tipp(t) for t in last_tipps]
    
    if user_tallies:
        best_score = max([ut.total_points for ut in user_tallies])
        best_tippers = [ut for ut in user_tallies if ut.total_points == best_score]

        #allinerseiger
        if len(best_tippers) == 1:
            print ("Best tipper was {} (+1)".format(best_tippers[0].tipper))
            best_tippers[0].bonus_points += 1
            best_tippers[0].is_best_tipp = True
            best_tippers[0].save()

    return user_tallies


def score_tipp(tipp):
    race_event = tipp.race_event
    tipper = tipp.tipper

    print("scoring race {} for {}".format(race_event, tipper))

    standard_points, bonus_points = racer_points(tipp, race_event)

    user_tally = TippPointTally(tipper=tipper, race_event=race_event, tipp=tipp)

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
        if start_number <= 20 and start_number % 2 == 1:
            group = 1
        elif start_number <= 20:
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
    correct_rank = False

    racer_start = race_event.podium.filter(racer=racer).first()
    if racer_start:
        start_number = racer_start.start_number
        start_group, start_group_multiplier = determine_start_group(race_event, start_number)

        if racer_start.rank <= 3:
            points = 1 * start_group_multiplier
        
        if racer_start.rank == position:
            correct_rank = True

        print ("{} - p{}: {}x, {}, {}".format(racer, position, start_group_multiplier, points, correct_rank))

    return points, correct_rank

def dnf_points(tipp, race_event):
    points = 0
    if tipp.alle_im_ziel and race_event.alle_im_ziel:
        points += 1
    elif race_event.dnfs.filter(racer=tipp.dnf).exists():
        points += 1

    return points

def ranking_bonus_points(number_correct):
    points = 0
    if number_correct == 1:
        points += 1
    if number_correct == 2:
        points += 0.5
    if number_correct == 3:
        points += 1

    return points

def racer_points(tipp, race_event):
    points1, correct1 = podium_points(tipp.place_1, 1, race_event)
    points2, correct2 = podium_points(tipp.place_2, 2, race_event)
    points3, correct3 = podium_points(tipp.place_3, 3, race_event)


    standard_points = points1 + points2 + points3

    number_correct = int(correct1) + int(correct2) + int(correct3)
    bonus_points = ranking_bonus_points(number_correct)

    bonus_points += dnf_points(tipp, race_event)

    print ("{} race points: sp: {}, bp: {}".format(tipp.tipper, standard_points, bonus_points))

    return standard_points, bonus_points


