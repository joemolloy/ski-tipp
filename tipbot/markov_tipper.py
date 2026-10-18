"""Markov tipper: a small ski-tipp bot built on a hidden Markov model of racer form.

Pure Python, no dependencies, reads a season database directly with sqlite3.

How it tipps a race:
1. Form (the Markov part). Each racer has a hidden form state per discipline, one of five
   levels. Between races the state moves by a transition matrix, over the summer by a second,
   looser one. Each race result is an emission: win, 2-3, 4-10, 11-30, out (not in the results,
   e.g. didn't qualify for run 2) or DNF. Parameters are shared per discipline and fitted with
   Baum-Welch; each racer's belief is filtered forward race by race, so a prediction only
   ever uses earlier results.
2. Race. The predicted form, form in the sister discipline, the racer's own decayed win and
   podium rate, an Elo-style rating, decayed World Cup points, venue form, age, the start group
   and, for speed races, the training runs give each starter a Plackett-Luce strength, fitted on
   past podiums. Podium chances follow exactly from those strengths. DNF chances come from the
   form model, the racer's own DNF rate, the discipline and start group, fitted on past DNFs.
3. Tipp. It picks the podium tipp and DNF pick with the highest expected ski-tipp points,
   using the real scoring: start group multipliers, rank and podium bonuses and alle im Ziel.
   So a 12x outsider can beat a safe favourite.

Usage (from the repo root):
    python tipbot/markov_tipper.py backtest                 # season-by-season out-of-sample test
    python tipbot/markov_tipper.py train                    # fit on all history, save tipbot/model.json
    python tipbot/markov_tipper.py form --discipline GS     # current form table
    python tipbot/markov_tipper.py predict --discipline GS --start-list soelden.csv
    python tipbot/markov_tipper.py predict --race 123456 --race-db ski-tipp-db.sqlite3
"""
import argparse
import csv
import json
import math
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(HERE, "data", "history.sqlite3")
DEFAULT_EXTRA = os.path.join(HERE, "data", "fis.sqlite3")
DEFAULT_MODEL = os.path.join(HERE, "model.json")

DISCIPLINES = {"Slalom": "SL", "Giant Slalom": "GS", "Super G": "SG", "Downhill": "DH"}
KIND_OF = {v: k for k, v in DISCIPLINES.items()}
FAMILY = {"SL": "tech", "GS": "tech", "SG": "speed", "DH": "speed"}
SIBLING = {"SL": "GS", "GS": "SL", "SG": "DH", "DH": "SG"}

WIN, PODIUM, TOP10, TOP30, OUT, DNF = range(6)
OUTCOMES = ["win", "2-3", "4-10", "11-30", "out", "dnf"]
STATES = ["dominant", "podium", "contender", "solid", "fringe"]
K = len(STATES)
NEW_WINTER = 1
PL_TOP = 3  # finishing places the race model is fitted on
DECAY = 0.9  # weight kept by older results in the racer's own win and podium record
SHRINK = 3  # results' worth of HMM prediction mixed into that record
RIDGE = 5.0  # L2 penalty on the race model weights
POINTS_DECAY = 0.85  # weight kept by older results in the decayed World Cup points average
VENUE_DECAY = 0.8
VENUE_PRIOR = 2  # results' worth of the racer's overall average mixed into a venue average
RATING_STEP = 0.3  # learning rate of the online Plackett-Luce rating
RATING_SUMMER = 0.8  # ratings shrink towards 0 over the summer
RATING_NEW = -1.0
RATING_DEPTH = 10  # finishing places each rating update looks at
USE_TRAINING = True
USE_AGE = True
FIT_WINTERS = None        # experiments: fit the race model on only the last N winters
RECENCY_HALF_LIFE = 4  # an older winter counts half as much every 4 winters (racing and seeding drift)
ABLATE = set()  # experiments: feature names to zero out
# Absence as "out": treating missed speed races, or long tech streaks, as no information
# (injury rather than form) tested slightly worse; returning racers do start rusty.
OUT_ONLY_TECH = False
MAX_OUT_STREAK = None
VENUE_DNF_PRIOR = 30  # starters' worth of the discipline's DNF rate mixed into a venue's
USE_VENUE_DNF = True
USE_ALL_IN_MODEL = True
ALL_IN_PRIOR = 4  # races' worth of the discipline's all-finish rate mixed into a venue's
WC_POINTS = [100, 80, 60, 50, 45, 40, 36, 32, 29, 26, 24, 22, 20, 18, 16, 15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1]

# start groups, as in tipp_scorer.determine_start_group
GROUP_MULTIPLIER = [0, 1, 3, 6, 12]
# The rules changed twice; the stored tallies match these eras exactly:
# up to 2020/21 alle im Ziel and a right 2nd or 3rd place scored 1 (now 0.5), and up to
# 2021/22 FIS seeded the speed favourites onto odd bibs, so odd bibs to 19 were the 1x group.
OLD_BONUS_UNTIL = 2020
OLD_SPEED_GROUPS_UNTIL = 2021


def rules(winter):
    """(points for a right 2nd or 3rd place, points for alle im Ziel) in that winter."""
    return (1.0, 1.0) if winter is not None and winter <= OLD_BONUS_UNTIL else (0.5, 0.5)


def start_group(family, bib, winter=None):
    if family == "speed" and winter is not None and winter <= OLD_SPEED_GROUPS_UNTIL:
        if bib <= 20:
            return 1 if bib % 2 else 2
        return 3 if bib <= 30 else 4
    if family == "tech":
        if bib <= 7:
            return 1
        if bib <= 15:
            return 2
        return 3 if bib <= 30 else 4
    if 6 <= bib <= 15:
        return 1
    if bib <= 5 or 16 <= bib <= 20:
        return 2
    return 3 if bib <= 30 else 4


def multiplier(family, bib, winter=None):
    return GROUP_MULTIPLIER[start_group(family, bib, winter)]


def outcome(rank, is_dnf):
    if is_dnf:
        return DNF
    if rank is None:
        return OUT
    if rank == 1:
        return WIN
    if rank <= 3:
        return PODIUM
    if rank <= 10:
        return TOP10
    return TOP30 if rank <= 30 else OUT


def winter_of(date):
    # races from July onwards belong to the winter starting that year, so world champs fold in
    return date.year if date.month >= 7 else date.year - 1


def winter_name(w):
    return f"{w}/{str(w + 1)[2:]}"


# ---------------------------------------------------------------- data

class Starter:
    __slots__ = ("racer", "bib", "rank", "dnf")

    def __init__(self, racer, bib, rank=None, dnf=False):
        self.racer, self.bib, self.rank, self.dnf = racer, bib, rank, dnf


class Race:
    def __init__(self, fis_id, name, kind, date, starters, multiplier=1, championship=False):
        self.fis_id, self.name, self.date = fis_id, name, date
        # world champs and olympics: nation quotas keep regulars out, so absence means nothing
        self.championship = championship
        self.training = None  # speed races: racer -> (best rank, best gap, runs) in the training runs
        self.disc = DISCIPLINES[kind]
        self.family = FAMILY[self.disc]
        self.winter = winter_of(date)
        self.starters = starters
        self.multiplier = multiplier
        self.venue = None  # canonical venue, see venue_key

    @property
    def finished(self):
        return any(s.rank == 1 for s in self.starters)


def parse_date(text):
    return datetime.fromisoformat(text[:19])


def racer_names(con):
    # result imports can append the ski brand on new lines, e.g. "GRAVIER Tiziano\n\n\nHead"
    return {fis_id: name.strip().split("\n")[0].strip() for fis_id, name in con.execute("select fis_id, name from skitipp_racer")}


VENUE_ALIASES = [("val d", "valdisere"), ("gardena", "valgardena"), ("groeden", "valgardena"),
                 ("bornio", "bormio"), ("grandvalira", "soldeu"), ("santa caterina", "santacaterina")]


def venue_key(location):
    """Canonical venue from the app's location text, which varies by season and import:
    "Val d'IsÃ¨re (FRA)", "Val d Isere (FRA)", "Kitzbuehel 2x (AUT)", "Bornio (Replaces Val Gardena) (ITA)"."""
    if not location:
        return None
    place = re.split(r"\(| - |- ", location)[0].lower()
    place = re.sub(r"[^a-z]+", " ", place.encode("ascii", "ignore").decode())
    for needle, key in VENUE_ALIASES:
        if needle in place:
            return key
    words = place.split()
    return words[0] if words else None


def is_championship(season_name):
    return bool(season_name) and any(w in season_name.lower() for w in ("champ", "olymp", "games"))


def load_history(db_path, extra=()):
    """Finished races in the four main disciplines, oldest first, plus racer names and the
    tippers' points. Extra databases (e.g. tipbot/data/fis.sqlite3, scraped from FIS) add older
    seasons and speed training runs."""
    races, names, human = {}, {}, defaultdict(dict)
    trainings = []
    for path in [db_path, *[e for e in extra if e and os.path.exists(e)]]:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        for k, v in racer_names(con).items():
            names.setdefault(k, v)
        rows = con.execute(
            "select r.fis_id, r.short_name, r.kind, r.race_date, r.points_multiplier, s.name, r.location, "
            "c.racer_id, c.start_number, c.rank, c.is_dnf "
            "from skitipp_raceevent r join skitipp_racecompetitor c on c.race_event_id = r.fis_id "
            "left join skitipp_season s on s.id = r.season_id "
            "where r.cancelled = 0 order by r.race_date, r.fis_id"
        )
        mine = {}
        for fis_id, name, kind, date, mult, season, location, racer, bib, rank, dnf in rows:
            if kind not in DISCIPLINES or (fis_id in races and fis_id not in mine):
                continue
            if fis_id not in mine:
                mine[fis_id] = races[fis_id] = Race(fis_id, name, kind, parse_date(date), [], mult or 1, is_championship(season))
                mine[fis_id].venue = venue_key(location)
            mine[fis_id].starters.append(Starter(racer, bib, rank, bool(dnf)))
        for race_id, tipper, pts in con.execute(
            "select p.race_event_id, u.username, p.standard_points + p.bonus_points "
            "from skitipp_tipppointtally p join auth_user u on u.id = p.tipper_id where p.tipp_id is not null"
        ):
            human[race_id][tipper] = pts
        if con.execute("select 1 from sqlite_master where name = 'tipbot_racer'").fetchone():
            BORN.update(con.execute("select racer, born from tipbot_racer where born is not null"))
        if con.execute("select 1 from sqlite_master where name = 'tipbot_training'").fetchone():
            trainings += con.execute(
                "select e.race_id, t.date, t.racer, t.rank, t.gap, t.dnf from tipbot_event e "
                "join tipbot_training t on t.event_id = e.event_id").fetchall()
        con.close()
    attach_training(races, trainings)
    finished = [r for r in races.values() if r.finished]
    finished.sort(key=lambda r: r.date)
    return finished, names, human


def attach_training(races, trainings):
    """race.training: racer -> (best rank, best gap in seconds, runs) over the event's training
    runs before the race day. None when the event had no training runs, or they weren't fetched."""
    by_race = defaultdict(lambda: defaultdict(list))
    for race_id, date, racer, rank, gap, dnf in trainings:
        race = races.get(race_id)
        if race and date < race.date.date().isoformat():
            by_race[race_id][racer].append((rank, gap, dnf))
    for race_id, runs in by_race.items():
        races[race_id].training = {
            racer: (min((r for r, _, _ in rs if r), default=None),
                    min((g for _, g, _ in rs if g is not None), default=None), len(rs))
            for racer, rs in runs.items()}


# ---------------------------------------------------------------- form HMM

def normalise(v):
    s = sum(v)
    return [x / s for x in v]


class FormHMM:
    """K hidden form states, two transition matrices (next race, next winter), 5 outcomes."""

    def __init__(self, pi, A, A_winter, B):
        self.pi, self.T, self.B = pi, [A, A_winter], B

    @classmethod
    def initial(cls):
        # states start spread from podium regular to fringe starter; Baum-Welch takes it from there
        best = [0.35, 0.25, 0.25, 0.05, 0.01, 0.09]
        worst = [0.0005, 0.001, 0.02, 0.20, 0.50, 0.279]
        B = [normalise([b + (w - b) * i / (K - 1) for b, w in zip(best, worst)]) for i in range(K)]
        pi = normalise([1 + 4 * i for i in range(K)])
        A = [[0.9 if i == j else 0.1 / (K - 1) for j in range(K)] for i in range(K)]
        A_winter = [[0.6 if i == j else 0.4 / (K - 1) for j in range(K)] for i in range(K)]
        return cls(pi, A, A_winter, B)

    def to_json(self):
        return dict(pi=self.pi, A=self.T[0], A_winter=self.T[1], B=self.B)

    @classmethod
    def from_json(cls, d):
        return cls(d["pi"], d["A"], d["A_winter"], d["B"])

    def step(self, belief, gap):
        T = self.T[gap]
        return [sum(belief[i] * T[i][j] for i in range(K)) for j in range(K)]

    def emit(self, state_dist):
        return [sum(state_dist[i] * self.B[i][o] for i in range(K)) for o in range(len(OUTCOMES))]

    def update(self, state_dist, obs):
        return normalise([state_dist[i] * self.B[i][obs] for i in range(K)])

    def fit(self, sequences, iterations=25, prior=0.5):
        """Baum-Welch. sequences: list of (observations, gaps) where gaps[t] is the transition into t."""
        for _ in range(iterations):
            pi_c = [prior] * K
            T_c = [[[prior] * K for _ in range(K)] for _ in range(2)]
            B_c = [[prior / len(OUTCOMES)] * len(OUTCOMES) for _ in range(K)]
            for obs, gaps in sequences:
                n = len(obs)
                alpha, scale = [], []
                a = [self.pi[i] * self.B[i][obs[0]] for i in range(K)]
                for t in range(n):
                    if t:
                        T = self.T[gaps[t]]
                        prev = alpha[-1]
                        a = [sum(prev[i] * T[i][j] for i in range(K)) * self.B[j][obs[t]] for j in range(K)]
                    c = sum(a)
                    alpha.append([x / c for x in a])
                    scale.append(c)
                beta = [1.0] * K
                for t in range(n - 1, -1, -1):
                    g = normalise([alpha[t][i] * beta[i] for i in range(K)])
                    for i in range(K):
                        B_c[i][obs[t]] += g[i]
                    if t == 0:
                        for i in range(K):
                            pi_c[i] += g[i]
                        break
                    T = self.T[gaps[t]]
                    eb = [self.B[j][obs[t]] * beta[j] for j in range(K)]
                    prev = alpha[t - 1]
                    counts = T_c[gaps[t]]
                    for i in range(K):
                        for j in range(K):
                            counts[i][j] += prev[i] * T[i][j] * eb[j] / scale[t]
                    beta = [sum(T[i][j] * eb[j] for j in range(K)) / scale[t] for i in range(K)]
            self.pi = normalise(pi_c)
            self.T = [[normalise(row) for row in T_c[g]] for g in range(2)]
            self.B = [normalise(row) for row in B_c]
        self._sort_states()
        return self

    def _sort_states(self):
        # keep states ordered best to worst so the names mean something
        order = sorted(range(K), key=lambda i: -(self.B[i][WIN] * 9 + self.B[i][PODIUM] * 3 + self.B[i][TOP10]))
        self.pi = [self.pi[i] for i in order]
        self.B = [self.B[i] for i in order]
        self.T = [[[T[i][j] for j in order] for i in order] for T in self.T]


class FormTracker:
    """Runs the HMMs forward through races, one belief per (racer, discipline)."""

    def __init__(self, hmms):
        self.hmms = hmms
        self.belief = {}          # (racer, disc) -> state distribution after last observation
        self.last_winter = {}     # (racer, disc) -> winter of last observation
        self.seen_this_winter = defaultdict(set)  # (disc, winter) -> racers
        self.record = {}          # (racer, disc) -> decayed [results, wins, podiums]
        self.rating = {}          # (racer, disc) -> (online Plackett-Luce rating, winter)
        self.points = {}          # (racer, disc) -> decayed (results, World Cup points)
        self.venue_points = {}    # (racer, disc, venue) -> decayed (results, World Cup points)
        self.scored = defaultdict(set)  # racer -> winters with World Cup points
        self.dnfs = {}            # (racer, disc) -> decayed (starts, DNFs)
        self.absent_streak = defaultdict(int)  # (racer, disc) -> races missed in a row
        self.course_all_in = defaultdict(lambda: [0.0, 0.0])  # (venue, disc) or disc -> decayed [races, all finished]
        self.course_dnfs = defaultdict(lambda: [0.0, 0.0])  # (venue, disc) or disc -> decayed [starts, DNFs], bibs 1-30

    def predict(self, racer, disc, winter):
        key = (racer, disc)
        hmm = self.hmms[disc]
        if key not in self.belief:
            return hmm.pi
        gap = NEW_WINTER if winter != self.last_winter[key] else 0
        return hmm.step(self.belief[key], gap)

    def emit_sibling(self, racer, race):
        disc = SIBLING[race.disc]
        return self.hmms[disc].emit(self.predict(racer, disc, race.winter))

    def ratings(self, racer, race):
        """Non-Markov extras: an Elo-style rating, decayed World Cup points and how much better
        or worse than usual the racer does at this venue."""
        n, pts = self.points.get((racer, race.disc), (0.0, 0.0))
        overall = pts / (n + 1)
        vn, vp = self.venue_points.get((racer, race.disc, race.venue), (0.0, 0.0))
        venue = (vp + VENUE_PRIOR * overall) / (vn + VENUE_PRIOR)
        return [self.get_rating(racer, race.disc, race.winter), math.log(1 + overall),
                math.log(1 + venue) - math.log(1 + overall)] + training_features(race, racer) + age_features(race, racer)

    def get_rating(self, racer, disc, winter):
        if (racer, disc) not in self.rating:
            return RATING_NEW
        r, w = self.rating[(racer, disc)]
        return r if w == winter else r * RATING_SUMMER

    def _observe_ratings(self, race):
        r = [self.get_rating(s.racer, race.disc, race.winter) for s in race.starters]
        ex = [math.exp(x) for x in r]
        grad = [0.0] * len(r)
        remaining = set(range(len(r)))
        finishers = sorted((i for i, s in enumerate(race.starters) if s.rank and not s.dnf),
                           key=lambda i: race.starters[i].rank)
        # one gradient step on the Plackett-Luce likelihood of the finishing order
        for i in finishers[:RATING_DEPTH]:
            total = sum(ex[j] for j in remaining)
            for j in remaining:
                grad[j] -= ex[j] / total
            grad[i] += 1
            remaining.discard(i)
        for k, s in enumerate(race.starters):
            n, d = self.dnfs.get((s.racer, race.disc), (0.0, 0.0))
            self.dnfs[(s.racer, race.disc)] = (DECAY * n + 1, DECAY * d + s.dnf)
            self.rating[(s.racer, race.disc)] = (r[k] + RATING_STEP * grad[k], race.winter)
            won = WC_POINTS[s.rank - 1] if s.rank and not s.dnf and s.rank <= 30 else 0
            n, pts = self.points.get((s.racer, race.disc), (0.0, 0.0))
            self.points[(s.racer, race.disc)] = (POINTS_DECAY * n + 1, POINTS_DECAY * pts + won)
            key = (s.racer, race.disc, race.venue)
            vn, vp = self.venue_points.get(key, (0.0, 0.0))
            self.venue_points[key] = (VENUE_DECAY * vn + 1, VENUE_DECAY * vp + won)

    def all_in_features(self, race, p_independent):
        n_all, k_all = self.course_all_in[race.disc]
        avg = (k_all + 1) / (n_all + 4)
        n, k = self.course_all_in[(race.venue, race.disc)]
        venue = logit((k + ALL_IN_PRIOR * avg) / (n + ALL_IN_PRIOR)) - logit(avg)
        return [1.0, logit(p_independent), venue, 1.0 if race.disc in ("SL", "DH") else 0.0]

    def venue_dnf(self, race):
        """How much more (or less) often the first 30 DNF on this course than in the discipline
        overall, as a logit difference. Some hills are much harder to get down than others."""
        if not USE_VENUE_DNF:
            return 0.0
        n_all, d_all = self.course_dnfs[race.disc]
        avg = (d_all + 1) / (n_all + 5)
        n, d = self.course_dnfs[(race.venue, race.disc)]
        return logit((d + VENUE_DNF_PRIOR * avg) / (n + VENUE_DNF_PRIOR)) - logit(avg)

    def dnf_rate(self, racer, disc, emission):
        """The racer's own recent DNF rate in the discipline, shrunk towards the form prediction."""
        n, d = self.dnfs.get((racer, disc), (0.0, 0.0))
        return (d + SHRINK * emission[DNF]) / (n + SHRINK)

    def eligible(self, racer, winter):
        # the tipp form lists the World Cup standings, so racers with points this winter or,
        # before the list is refreshed, last winter
        return winter in self.scored[racer] or winter - 1 in self.scored[racer]

    def observe(self, race):
        self._observe_ratings(race)
        top30 = [s for s in race.starters if s.bib <= 30]
        if not race.championship:
            for key in (race.disc, (race.venue, race.disc)):
                c = self.course_all_in[key]
                c[0] = DECAY * c[0] + 1
                c[1] = DECAY * c[1] + (not any(s.dnf for s in top30))
        for key in (race.disc, (race.venue, race.disc)):
            c = self.course_dnfs[key]
            c[0] = DECAY * c[0] + len(top30)
            c[1] = DECAY * c[1] + sum(s.dnf for s in top30)
        if not race.championship:
            for s in race.starters:
                if s.rank and not s.dnf and s.rank <= 30:
                    self.scored[s.racer].add(race.winter)
        present = set()
        for s in race.starters:
            self._observe(s.racer, race, outcome(s.rank, s.dnf))
            present.add(s.racer)
        for racer in absentees(race, self.seen_this_winter, present, self.absent_streak):
            self._observe(racer, race, OUT)

    def _observe(self, racer, race, obs):
        key = (racer, race.disc)
        prior = self.predict(racer, race.disc, race.winter)
        self.belief[key] = self.hmms[race.disc].update(prior, obs)
        self.last_winter[key] = race.winter
        n, wins, pods = self.record.get(key, (0.0, 0.0, 0.0))
        self.record[key] = (DECAY * n + 1, DECAY * wins + (obs == WIN), DECAY * pods + (obs in (WIN, PODIUM)))

    def own_record(self, racer, disc, emission):
        """The racer's own recent win and podium rates, shrunk towards the form prediction.
        The form states are shared by everyone, this is what tells Odermatt apart from the
        rest of the top group."""
        n, wins, pods = self.record.get((racer, disc), (0.0, 0.0, 0.0))
        p_win = (wins + SHRINK * emission[WIN]) / (n + SHRINK)
        p_pod = (pods + SHRINK * (emission[WIN] + emission[PODIUM])) / (n + SHRINK)
        return p_win, p_pod


def absentees(race, seen, present, streak):
    """Racers to count as "out" of a race they're missing from. Tech results leave out racers
    who didn't make the second run, so a racer seen earlier this winter but missing probably
    didn't qualify. But several misses in a row mean injury, not bad form, so only the first
    MAX_OUT_STREAK count. Speed results list every finisher, so missing means didn't start.
    Championships have nation quotas, so missing there means nothing. Updates seen and streak."""
    for racer in present:
        streak[(racer, race.disc)] = 0
    if race.championship:
        return []
    out = []
    key = (race.disc, race.winter)
    if race.family == "tech" or not OUT_ONLY_TECH:
        for racer in seen[key] - present:
            streak[(racer, race.disc)] += 1
            if not MAX_OUT_STREAK or streak[(racer, race.disc)] <= MAX_OUT_STREAK:
                out.append(racer)
    seen[key] |= present
    return out


def form_sequences(races):
    """Per (racer, discipline) observation sequences, matching FormTracker.observe."""
    seqs = defaultdict(lambda: ([], []))
    last_winter = {}
    seen, streak = defaultdict(set), defaultdict(int)
    for race in races:
        present = {}
        for s in race.starters:
            present[s.racer] = outcome(s.rank, s.dnf)
        for racer in absentees(race, seen, set(present), streak):
            present[racer] = OUT
        for racer, obs in present.items():
            key = (racer, race.disc)
            o, g = seqs[key]
            g.append(NEW_WINTER if key in last_winter and last_winter[key] != race.winter else 0)
            o.append(obs)
            last_winter[key] = race.winter
    by_disc = defaultdict(list)
    for (racer, disc), seq in seqs.items():
        by_disc[disc].append(seq)
    return by_disc


# ---------------------------------------------------------------- race model

def logit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def features(family, emission, sibling, record, ratings, bib, winter=None):
    """Plackett-Luce features for one starter. Start groups are one-hot, group 1 is the baseline."""
    p_win = emission[WIN]
    p_pod = p_win + emission[PODIUM]
    p_top10 = p_pod + emission[TOP10]
    p_top30 = p_top10 + emission[TOP30]
    groups = [0.0, 0.0, 0.0]
    g = start_group(family, bib, winter)
    if g > 1:
        groups[g - 2] = 1.0
    sib = math.log(sibling[WIN] + sibling[PODIUM] + sibling[TOP10] + 1e-3)
    own = [math.log(record[0] + 1e-4), math.log(record[1] + 1e-4)]
    x = [math.log(p_win + 1e-4), math.log(p_pod + 1e-4), math.log(p_top10 + 1e-3), math.log(p_top30 + 1e-3), sib] + own + ratings + groups
    return [0.0 if ABLATE and FEATURE_NAMES[i] in ABLATE else v for i, v in enumerate(x)]


BORN = {}  # racer -> birth year, from tipbot/data/fis.sqlite3


def age_features(race, racer):
    """Distance from a peak age of 29, and its square; zero when the birth year is unknown."""
    if not USE_AGE or racer not in BORN:
        return [0.0, 0.0]
    a = (race.winter + 1 - BORN[racer] - 29) / 5
    return [a, a * a]


def training_features(race, racer):
    """Speed races: how the racer went in the event's training runs before race day. Zero when
    there were none, so tech races and untrained events don't move these weights."""
    if not USE_TRAINING or not race.training:
        return [0.0, 0.0, 0.0]
    if racer not in race.training:
        return [0.0, 0.0, 1.0]
    rank, gap, _ = race.training[racer]
    return [math.log(rank or 60) - math.log(10), min(gap if gap is not None else 3.0, 3.0), 0.0]


FEATURE_NAMES = ["log P(win)", "log P(podium)", "log P(top10)", "log P(top30)", "sister discipline log P(top10)", "own log win rate", "own log podium rate", "rating", "log decayed WC points", "venue form",
                 "training log best rank", "training best gap (s)", "skipped training", "age - 29 (/5)", "age squared", "start group 2", "start group 3", "start group 4"]


def solve(H, g):
    """Solve H x = g for a small dense system (Gaussian elimination with pivoting)."""
    n = len(g)
    M = [row[:] + [g[i]] for i, row in enumerate(H)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[p] = M[p], M[c]
        for r in range(n):
            if r != c and M[c][c]:
                f = M[r][c] / M[c][c]
                M[r] = [a - f * b for a, b in zip(M[r], M[c])]
    return [M[i][n] / M[i][i] for i in range(n)]


def fit_plackett_luce(samples, n_features, top=5, ridge=1.0, iterations=12):
    """samples: list of (X, order, weight) with X the starters' features, order the finishing order
    and weight how much the race counts."""
    w = [0.0] * n_features
    w[0] = 1.0
    for _ in range(iterations):
        grad = [-ridge * x for x in w]
        H = [[ridge if i == j else 0.0 for j in range(n_features)] for i in range(n_features)]
        for X, order, weight in samples:
            ex = [math.exp(min(sum(a * b for a, b in zip(w, x)), 50.0)) for x in X]
            remaining = set(range(len(X)))
            for k in order[:top]:
                total = sum(ex[j] for j in remaining)
                mean = [sum(ex[j] * X[j][f] for j in remaining) / total for f in range(n_features)]
                for f in range(n_features):
                    grad[f] += weight * (X[k][f] - mean[f])
                for j in remaining:
                    pj = weight * ex[j] / total
                    d = [X[j][f] - mean[f] for f in range(n_features)]
                    for a in range(n_features):
                        da = pj * d[a]
                        row = H[a]
                        for b in range(n_features):
                            row[b] += da * d[b]
                remaining.discard(k)
        step = solve(H, grad)
        w = [a + b for a, b in zip(w, step)]
        if max(abs(s) for s in step) < 1e-4:
            break
    return w


def race_weight(winter, last):
    """How much a past race counts when fitting the race and DNF models (the form model always
    sees everything). FIT_WINTERS keeps only the latest winters; RECENCY_HALF_LIFE fades older ones."""
    if FIT_WINTERS and winter <= last - FIT_WINTERS:
        return 0.0
    return 0.5 ** ((last - winter) / RECENCY_HALF_LIFE) if RECENCY_HALF_LIFE else 1.0


def fit_logistic(X, ys, weights=None, ridge=1.0, iterations=15):
    """Logistic regression by Newton's method, for DNF calibration."""
    n = len(X[0])
    w = [0.0] * n
    for _ in range(iterations):
        grad = [-ridge * a for a in w]
        H = [[ridge if i == j else 0.0 for j in range(n)] for i in range(n)]
        for x, y, c in zip(X, ys, weights or [1.0] * len(ys)):
            p = 1 / (1 + math.exp(-sum(a * b for a, b in zip(w, x))))
            v = c * p * (1 - p)
            for i in range(n):
                grad[i] += c * (y - p) * x[i]
                for j in range(n):
                    H[i][j] += v * x[i] * x[j]
        step = solve(H, grad)
        w = [a + b for a, b in zip(w, step)]
        if max(abs(a) for a in step) < 1e-5:
            break
    return w


def dnf_features(race, emission, bib, own_dnf_rate, venue_dnf=0.0):
    # DNF depends on form and the racer's own record, on the discipline (slalom more than GS)
    # and on the start group: late starters race on a rutted course
    groups = [0.0, 0.0, 0.0]
    g = start_group(race.family, bib, race.winter)
    if g > 1:
        groups[g - 2] = 1.0
    return [1.0, logit(emission[DNF]), logit(own_dnf_rate), 1.0 if race.disc in ("SL", "DH") else 0.0, venue_dnf] + groups


class RaceModel:
    def __init__(self, hmms, weights, dnf_calibration, late_podium_rate, all_in=None):
        self.hmms = hmms
        self.all_in = all_in or {}        # family -> logistic weights for "the first 30 all finish"
        self.weights = weights            # family -> PL weights
        self.dnf_calibration = dnf_calibration  # family -> logistic weights
        self.late_podium_rate = late_podium_rate  # family -> podium places per race won from bib 31+

    def to_json(self):
        return dict(
            hmms={d: h.to_json() for d, h in self.hmms.items()},
            weights=self.weights,
            dnf_calibration=self.dnf_calibration,
            late_podium_rate=self.late_podium_rate,
            all_in=self.all_in,
        )

    @classmethod
    def from_json(cls, d):
        return cls({k: FormHMM.from_json(v) for k, v in d["hmms"].items()}, d["weights"],
                   d["dnf_calibration"], d["late_podium_rate"], d.get("all_in"))

    @classmethod
    def train(cls, races, log=print):
        by_disc = form_sequences(races)
        hmms = {}
        for disc in DISCIPLINES.values():
            log(f"  fitting form HMM for {disc} on {sum(len(o) for o, _ in by_disc[disc])} results")
            hmms[disc] = FormHMM.initial().fit(by_disc[disc])

        tracker = FormTracker(hmms)
        pl_samples = defaultdict(list)
        dnf_X, dnf_ys = defaultdict(list), defaultdict(list)
        late_podiums, family_races = defaultdict(int), defaultdict(int)
        last = max(r.winter for r in races)
        for race in races:
            weight = race_weight(race.winter, last)
            if not weight:
                tracker.observe(race)
                continue
            family_races[race.family] += 1
            late_podiums[race.family] += sum(1 for s in race.starters if s.bib > 30 and not s.dnf and s.rank and s.rank <= 3)
            # older databases only kept DNFs from bib 30 and better, which would skew the DNF model
            dnf_complete = race.family == "speed" or any(s.dnf and s.bib > 30 for s in race.starters)
            # Plackett-Luce over every starter, DNFs included, so DNF risk is part of the strength
            X = []
            for s in race.starters:
                em = tracker.hmms[race.disc].emit(tracker.predict(s.racer, race.disc, race.winter))
                X.append(features(race.family, em, tracker.emit_sibling(s.racer, race), tracker.own_record(s.racer, race.disc, em),
                                  tracker.ratings(s.racer, race), s.bib, race.winter))
                dnf_X[race.family].append((dnf_complete, dnf_features(race, em, s.bib, tracker.dnf_rate(s.racer, race.disc, em), tracker.venue_dnf(race))))
                dnf_ys[race.family].append((dnf_complete, 1.0 if s.dnf else 0.0, weight))
            finishers = [i for i, s in enumerate(race.starters) if not s.dnf and s.rank]
            order = sorted(finishers, key=lambda i: race.starters[i].rank)
            pl_samples[race.family].append((X, order, weight))
            tracker.observe(race)

        weights, calibration = {}, {}
        for family in ("tech", "speed"):
            log(f"  fitting race model for {family} on {len(pl_samples[family])} races")
            weights[family] = fit_plackett_luce(pl_samples[family], len(FEATURE_NAMES), top=PL_TOP, ridge=RIDGE)
            use_all = not any(c for c, _ in dnf_X[family])  # no complete records: take what there is
            X = [x for c, x in dnf_X[family] if c or use_all]
            y = [v for c, v, _ in dnf_ys[family] if c or use_all]
            wy = [k for c, _, k in dnf_ys[family] if c or use_all]
            calibration[family] = fit_logistic(X, y, wy)
        late_rate = {f: late_podiums[f] / max(family_races[f], 1) for f in family_races}
        model = cls(hmms, weights, calibration, late_rate)
        if USE_ALL_IN_MODEL:
            model.all_in = model.fit_all_in(races, last)
        return model

    def fit_all_in(self, races, last):
        """Alle im Ziel as its own race-level model. Multiplying thirty finish chances together
        compounds small errors (worse than a constant rate in speed), and some hills are far
        kinder than others, so fit it on past races: does the first 30 all finish?"""
        tracker = FormTracker(self.hmms)
        X, y, wt = defaultdict(list), defaultdict(list), defaultdict(list)
        for race in races:
            weight = race_weight(race.winter, last)
            if weight and any(s.bib > 30 and s.dnf for s in race.starters) or race.family == "speed":
                odds = RaceOdds(race, self.starter_probabilities(tracker, race))
                X[race.family].append(tracker.all_in_features(race, odds.p_all_in))
                y[race.family].append(0.0 if any(s.dnf and s.bib <= 30 for s in race.starters) else 1.0)
                wt[race.family].append(weight)
            tracker.observe(race)
        return {f: fit_logistic(X[f], y[f], wt[f]) for f in X if sum(y[f]) >= 3}

    def starter_probabilities(self, tracker, race):
        """Per starter: (PL strength, DNF probability, form emission)."""
        w = self.weights[race.family]
        wd = self.dnf_calibration[race.family]
        out = []
        for s in race.starters:
            em = self.hmms[race.disc].emit(tracker.predict(s.racer, race.disc, race.winter))
            x = features(race.family, em, tracker.emit_sibling(s.racer, race), tracker.own_record(s.racer, race.disc, em),
                         tracker.ratings(s.racer, race), s.bib, race.winter)
            u = sum(a * b for a, b in zip(w, x))
            x = dnf_features(race, em, s.bib, tracker.dnf_rate(s.racer, race.disc, em), tracker.venue_dnf(race))
            p_dnf = 1 / (1 + math.exp(-sum(a * b for a, b in zip(wd, x))))
            out.append((u, p_dnf, em))
        return out

    def odds(self, tracker, race, boosts=None):
        probs = self.starter_probabilities(tracker, race)
        # How many bib 31+ starters a database lists varies by season (non-qualifiers and, in
        # older seasons, DNFs are missing), so a per-starter strength can't be trusted for them.
        # Instead scale them together so they win podium places at the historical rate per race.
        # Only ever down: a short start list with one late starter mustn't hand him the lot.
        late = [i for i, s in enumerate(race.starters) if s.bib > 30]
        target = self.late_podium_rate.get(race.family, 0)
        if late and target > 0:
            for _ in range(4):
                odds = RaceOdds(race, probs)
                have = sum(odds.p_podium[i] for i in late)
                shift = min(0.0, math.log(target / have))
                if shift > -1e-3:
                    break
                probs = [(u + shift, p, em) if i in late else (u, p, em) for i, (u, p, em) in enumerate(probs)]
        # your own knowledge: a boost of 2 roughly doubles a racer's podium chance
        for i, s in enumerate(race.starters):
            if boosts and s.racer in boosts:
                u, p, em = probs[i]
                probs[i] = (u + math.log(boosts[s.racer]), p, em)
        odds = RaceOdds(race, probs)
        if race.family in self.all_in:
            x = tracker.all_in_features(race, odds.p_all_in)
            odds.p_all_in = 1 / (1 + math.exp(-sum(a * b for a, b in zip(self.all_in[race.family], x))))
        return odds


class RaceOdds:
    """Exact podium probabilities under Plackett-Luce, and DNF probabilities, for one race."""

    def __init__(self, race, probs):
        self.race = race
        top = max(u for u, _, _ in probs)
        w = self.w = [math.exp(u - top) for u, _, _ in probs]
        W = self.W = sum(w)
        n = len(w)
        # P(i 2nd) = w_i * sum_{j != i} w_j / (W (W - w_j)), P(i 3rd) similarly over ordered pairs
        s1 = [wj / (W * (W - wj)) for wj in w]
        S1 = sum(s1)
        c = [[0.0] * n for _ in range(n)]
        for j in range(n):
            for k in range(n):
                if j != k:
                    c[j][k] = w[j] / W * w[k] / (W - w[j]) / (W - w[j] - w[k])
        C = sum(map(sum, c))
        self.p_rank = []
        for i in range(n):
            touching = sum(c[i]) + sum(c[j][i] for j in range(n))
            self.p_rank.append([w[i] / W, w[i] * (S1 - s1[i]), w[i] * (C - touching)])
        self.p_podium = [sum(r) for r in self.p_rank]
        self.p_dnf = [p for _, p, _ in probs]
        self.p_all_in = 1.0
        for st, p in zip(race.starters, self.p_dnf):
            if st.bib <= 30:
                self.p_all_in *= 1 - p
        self.mult = [multiplier(race.family, st.bib, race.winter) for st in race.starters]
        self.place_bonus, self.all_in_points = rules(race.winter)
        self.eligible = None
        self._pairs = {}

    def ordered(self, a, b, c):
        w, W = self.w, self.W
        return w[a] / W * w[b] / (W - w[a]) * w[c] / (W - w[a] - w[b])

    def triple(self, a, b, c):
        o = self.ordered
        return o(a, b, c) + o(a, c, b) + o(b, a, c) + o(b, c, a) + o(c, a, b) + o(c, b, a)

    def pair(self, a, b):
        key = (min(a, b), max(a, b))
        if key not in self._pairs:
            self._pairs[key] = sum(self.triple(a, b, k) for k in range(len(self.w)) if k != a and k != b)
        return self._pairs[key]

    def expected_points(self, a, b, c):
        e = sum(self.mult[i] * self.p_podium[i] for i in (a, b, c))
        e += self.p_rank[a][0] + self.place_bonus * (self.p_rank[b][1] + self.p_rank[c][2])
        # podium bonus: 0.5 for exactly two right, 1 for three = 0.5 * sum of pairs - 0.5 * triple
        return e + 0.5 * (self.pair(a, b) + self.pair(a, c) + self.pair(b, c)) - 0.5 * self.triple(a, b, c)

    def tipp_value(self, podium, dnf):
        """Expected points of any tipp: racers as FIS codes, dnf None for alle im Ziel. A racer
        who isn't in the field (not starting, or missing from a guessed start list) scores nothing."""
        index = {s.racer: i for i, s in enumerate(self.race.starters)}
        picks = [index.get(r) for r in podium]
        e = sum(self.mult[i] * self.p_podium[i] for i in picks if i is not None)
        for pos, i in enumerate(picks):
            if i is not None:
                e += self.p_rank[i][pos] * (1 if pos == 0 else self.place_bonus)
        present = [i for i in picks if i is not None]
        pairs = sum(self.pair(a, b) for k, a in enumerate(present) for b in present[k + 1:])
        e += 0.5 * pairs - (0.5 * self.triple(*present) if len(present) == 3 else 0)
        if dnf is None:
            return e + self.all_in_points * self.p_all_in
        return e + (self.p_dnf[index[dnf]] if dnf in index else 0.0)

    def choices(self):
        """Starters a tipper can pick: the app only offers racers in the World Cup standings."""
        allowed = [i for i in range(len(self.mult)) if self.eligible is None or self.eligible[i]]
        return allowed if len(allowed) >= 4 else list(range(len(self.mult)))

    def best_podium(self, candidates=14):
        return self.best_podiums(1, candidates)[0]

    def best_podiums(self, k=3, candidates=14):
        """The k best podium tipps with different racers, each in its best order, as
        [((a, b, c), expected points)]. Reorderings of the same three would only differ by
        hundredths, so they don't count as alternatives."""
        value = lambda i: self.mult[i] * self.p_podium[i] + self.p_rank[i][0]
        pool = sorted(self.choices(), key=value, reverse=True)[:candidates]
        best = {}
        for t in ((a, b, c) for a in pool for b in pool for c in pool if len({a, b, c}) == 3):
            e = self.expected_points(*t)
            key = frozenset(t)
            if key not in best or e > best[key][1]:
                best[key] = (t, e)
        return sorted(best.values(), key=lambda te: -te[1])[:k]

    def favourites(self):
        """Baseline: the three most likely podium finishers, ordered by win chance."""
        top = sorted(self.choices(), key=lambda i: self.p_podium[i], reverse=True)[:3]
        return tuple(sorted(top, key=lambda i: -self.p_rank[i][0]))

    def best_dnf(self):
        """(starter index or None for alle im Ziel, expected DNF points)."""
        i = max(self.choices(), key=lambda i: self.p_dnf[i])
        if self.all_in_points * self.p_all_in > self.p_dnf[i]:
            return None, self.all_in_points * self.p_all_in
        return i, self.p_dnf[i]


def score(race, podium_racers, dnf_racer):
    """Ski-tipp points (standard + bonus, before multiplier and alleinsieger) of a tipp."""
    result = {s.racer: s for s in race.starters}
    place_bonus, all_in_points = rules(race.winter)
    pts, on, rank_bonus = 0.0, 0, 0.0
    for pos, racer in enumerate(podium_racers, 1):
        s = result.get(racer)
        if s and not s.dnf and s.rank and s.rank <= 3:
            pts += multiplier(race.family, s.bib, race.winter)
            on += 1
            if s.rank == pos:
                rank_bonus += 1 if pos == 1 else place_bonus
    pts += rank_bonus + {2: 0.5, 3: 1}.get(on, 0)
    if dnf_racer is None:
        if not any(s.dnf and s.bib <= 30 for s in race.starters):
            pts += all_in_points
    elif result.get(dnf_racer) and result[dnf_racer].dnf:
        pts += 1
    return pts


def tipp(model, tracker, race, boosts=None, eligible=None):
    """eligible: the racers the tipp form offers, if known (the app's active racers); otherwise
    estimated from who has scored World Cup points this winter or last."""
    sim = model.odds(tracker, race, boosts)
    if eligible is not None:
        sim.eligible = [s.racer in eligible for s in race.starters]
    else:
        sim.eligible = [tracker.eligible(s.racer, race.winter) for s in race.starters]
    (a, b, c), e_pod = sim.best_podium()
    dnf_i, e_dnf = sim.best_dnf()
    racers = [race.starters[i].racer for i in (a, b, c)]
    dnf = None if dnf_i is None else race.starters[dnf_i].racer
    return racers, dnf, e_pod + e_dnf, sim


# ---------------------------------------------------------------- commands

def cmd_backtest(args):
    races, names, human = load_history(args.db, args.extra_db)
    winters = sorted({r.winter for r in races})
    if args.scheme == "rolling":
        test_winters = [w for w in winters if w >= winters[0] + args.min_train]
    else:
        test_winters = winters
    print(f"{len(races)} finished races across winters {winter_name(winters[0])}-{winter_name(winters[-1])}, "
          f"{args.scheme} scheme\n")
    rows = []
    champs_bot, champs_humans = [], defaultdict(list)
    for w in test_winters:
        # rolling: only earlier winters, as in real use. loo: every other winter, so more training
        # data and more test races, but the shared parameters have seen the future
        train = [r for r in races if (r.winter < w if args.scheme == "rolling" else r.winter != w)]
        print(f"{winter_name(w)}: training on {len(train)} races")
        model = RaceModel.train(train, log=lambda m: None)
        tracker = FormTracker(model.hmms)
        model_pts, fav_pts, expected, humans = [], [], [], defaultdict(list)
        sole_best = 0
        for race in races:
            if race.winter == w:
                podium, dnf, exp, sim = tipp(model, tracker, race)
                pts = score(race, podium, dnf)
                fav = [race.starters[i].racer for i in sim.favourites()]
                model_pts.append(pts)
                expected.append(exp)
                fav_pts.append(score(race, fav, dnf))
                for tipper, hp in human.get(race.fis_id, {}).items():
                    humans[tipper].append(hp)
                if race.championship and human.get(race.fis_id):
                    champs_bot.append(pts)
                    for tipper, hp in human[race.fis_id].items():
                        champs_humans[tipper].append(hp)
                if human.get(race.fis_id) and pts > max(human[race.fis_id].values()):
                    sole_best += 1
                if args.verbose:
                    print(f"  {race.date:%Y-%m-%d} {race.name:9} tipp {', '.join(names.get(r, r) for r in podium):60}"
                          f" dnf {names.get(dnf, 'alle im Ziel'):22} exp {exp:4.1f} got {pts:4.1f}")
            if race.winter > w:
                break
            tracker.observe(race)
        n = len(model_pts)
        regulars = {t: sum(p) / len(p) for t, p in humans.items() if len(p) >= n * 0.4}
        ranked = sorted(regulars.values(), reverse=True)
        mppr = sum(model_pts) / n
        rows.append(dict(
            winter=winter_name(w), races=n, model=mppr, expected=sum(expected) / n,
            favourites=sum(fav_pts) / n,
            human_avg=sum(ranked) / len(ranked) if ranked else None,
            human_best=ranked[0] if ranked else None,
            rank=(1 + sum(1 for h in ranked if h > mppr), len(ranked) + 1) if ranked else None,
            sole_best=sole_best,
        ))

    print("\npoints per race (standard + bonus, out of sample)\n")
    print(f"{'winter':8} {'races':>5} {'markov':>7} {'expect':>7} {'favs':>6} {'humans':>7} {'best':>6}  rank   beat everyone")
    for r in rows:
        h = lambda v: f"{v:6.2f}" if v is not None else "     -"
        rank = f"{r['rank'][0]:>2}/{r['rank'][1]:<2}" if r["rank"] else "   - "
        print(f"{r['winter']:8} {r['races']:5} {r['model']:7.2f} {r['expected']:7.2f} {r['favourites']:6.2f}"
              f" {h(r['human_avg'])}  {h(r['human_best'])}  {rank}  {r['sole_best'] if r['rank'] else '-'}")
    print("\nmarkov: this bot. expect: what it expected to score. favs: same model but tipping the three most"
          "\nlikely podium finishers. humans: average regular tipper. best: best regular tipper that winter."
          "\nrank: where the bot would have finished among regular tippers.")
    if champs_bot:
        n = len(champs_bot)
        ppr = sorted((sum(p) / len(p) for p in champs_humans.values() if len(p) >= n * 0.4), reverse=True)
        bot = sum(champs_bot) / n
        print(f"\nworld champs and olympics only: {n} races, bot {bot:.2f}, average tipper {sum(ppr) / len(ppr):.2f}, "
              f"best {ppr[0]:.2f}, bot would rank {1 + sum(1 for h in ppr if h > bot)}/{len(ppr) + 1}")


def cmd_train(args):
    races, _, _ = load_history(args.db, args.extra_db)
    print(f"training on {len(races)} races")
    model = RaceModel.train(races)
    with open(args.model, "w") as f:
        json.dump(model.to_json(), f, indent=1)
    print(f"saved {args.model} ({os.path.getsize(args.model) / 1024:.1f} KB)\n")
    describe(model)


def describe(model):
    for disc, hmm in model.hmms.items():
        print(f"{KIND_OF[disc]} form states")
        print(f"  {'':10}" + "".join(f"{o:>8}" for o in OUTCOMES) + "   next race: " + " ".join(f"{s[:4]:>5}" for s in STATES))
        for i, s in enumerate(STATES):
            print(f"  {s:10}" + "".join(f"{p:8.2f}" for p in hmm.B[i]) + "              "
                  + " ".join(f"{p:5.2f}" for p in hmm.T[0][i]))
        print("  over the summer: " + "  ".join(f"{s} stays {hmm.T[1][i][i]:.2f}" for i, s in enumerate(STATES)))
        print()
    for family, w in model.weights.items():
        print(f"{family} race weights: " + ", ".join(f"{n} {v:+.2f}" for n, v in zip(FEATURE_NAMES, w)))


def load_model(args, races):
    if os.path.exists(args.model):
        with open(args.model) as f:
            return RaceModel.from_json(json.load(f))
    print("no saved model, training one", file=sys.stderr)
    return RaceModel.train(races, log=lambda m: print(m, file=sys.stderr))


def tipps_for(upcoming, db_path, extra=(), model_path=DEFAULT_MODEL, exclude=(), eligible=None, trainings=None, now=None,
              current=None):
    """Tipp upcoming races, for the app's tipbot page.

    upcoming: dicts with fis_id, kind, date, name, location and starters ([(racer, bib)], empty
    when the start list isn't out). exclude: racers not starting. eligible: racers the tipp form
    offers. trainings: fis_id -> [(date, racer, rank, gap, dnf)] training runs. current: fis_id ->
    (podium, dnf) of the user's tipp, dnf None for alle im Ziel; it gets its expected points and
    the index of the matching option, if any.
    Returns one dict per race, and the racer names."""
    races, names, _ = load_history(db_path, extra)
    with open(model_path) as f:
        model = RaceModel.from_json(json.load(f))
    tracker = tracker_until(model, races, now or datetime.now())
    exclude = set(exclude)
    out = []
    for u in upcoming:
        if u["kind"] not in DISCIPLINES:
            continue
        race = Race(u["fis_id"], u["name"], u["kind"], u["date"], [])
        race.venue = venue_key(u["location"])
        listed = [Starter(r, b) for r, b in u.get("starters") or [] if r not in exclude]
        race.starters = listed or guessed_start_list(model, tracker, race.disc, race.winter, names, exclude=exclude)
        runs = (trainings or {}).get(u["fis_id"])
        if runs:
            attach_training({race.fis_id: race}, [(race.fis_id, d, r, rank, gap, dnf) for d, r, rank, gap, dnf in runs])
        podium, dnf, exp, odds = tipp(model, tracker, race, eligible=eligible)
        order = sorted(range(len(race.starters)), key=lambda i: -odds.p_podium[i])
        dnf_points = exp - odds.best_podiums(1)[0][1]
        options = [dict(podium=[race.starters[i].racer for i in t], expected=e + dnf_points) for t, e in odds.best_podiums(3)]
        mine = (current or {}).get(race.fis_id)
        if mine:
            mine_podium, mine_dnf = list(mine[0]), mine[1]
            match = next((k for k, o in enumerate(options) if o["podium"] == mine_podium and mine_dnf == dnf), None)
            mine = dict(podium=mine_podium, dnf=mine_dnf, expected=odds.tipp_value(mine_podium, mine_dnf), option=match)
        out.append(dict(
            fis_id=race.fis_id, podium=podium, dnf=dnf, expected=exp, options=options, current=mine, guessed_bibs=not listed,
            training_runs=len({d for d, *_ in runs}) if runs else 0,
            contenders=[dict(racer=race.starters[i].racer, bib=race.starters[i].bib, mult=odds.mult[i],
                             p_win=odds.p_rank[i][0], p_podium=odds.p_podium[i], p_dnf=odds.p_dnf[i]) for i in order[:12]],
            bib={s.racer: s.bib for s in race.starters}, mult={s.racer: m for s, m in zip(race.starters, odds.mult)},
            p_dnf={s.racer: p for s, p in zip(race.starters, odds.p_dnf)}, p_all_in=odds.p_all_in,
        ))
    return out, names


def tracker_until(model, races, date):
    tracker = FormTracker(model.hmms)
    for race in races:
        if race.date < date:
            tracker.observe(race)
    return tracker


def cmd_form(args):
    races, names, _ = load_history(args.db, args.extra_db)
    model = load_model(args, races)
    date = datetime.fromisoformat(args.date) if args.date else datetime.now()
    tracker = tracker_until(model, races, date)
    winter = winter_of(date)
    recent = {k[0] for k, w in tracker.last_winter.items() if k[1] == args.discipline and w >= winter - 1}
    rows = []
    for racer in recent:
        state = tracker.predict(racer, args.discipline, winter)
        em = model.hmms[args.discipline].emit(state)
        rows.append((em[WIN] + em[PODIUM], racer, state, em))
    rows.sort(reverse=True)
    print(f"{KIND_OF[args.discipline]} form going into {date:%Y-%m-%d}\n")
    print(f"{'racer':28}" + "".join(f"{s:>10}" for s in STATES) + f"{'P(win)':>9}{'P(podium)':>11}{'P(dnf)':>8}")
    for _, racer, state, em in rows[:args.top]:
        print(f"{names.get(racer, racer):28}" + "".join(f"{p:10.2f}" for p in state) + f"{em[WIN]:9.2f}{em[WIN] + em[PODIUM]:11.2f}{em[DNF]:8.2f}")


def racer_lookup(names):
    """Find a racer by FIS code, full name or surname, as FIS writes them."""
    by_name = {}
    for fis_id, name in names.items():
        by_name[name.lower()] = fis_id
        by_name.setdefault(name.split(" ")[0].lower(), fis_id)
    return lambda who: int(who) if who.isdigit() else by_name.get(who.strip().lower())


def read_start_list(path, names):
    find = racer_lookup(names)
    starters = []
    with open(path) as f:
        for row in csv.reader(f):
            if not row or not row[0].strip().isdigit():
                continue
            bib, who = int(row[0]), row[1].strip()
            racer = find(who)
            if racer is None:
                sys.exit(f"unknown racer {who!r} in {path}, use the FIS code instead")
            starters.append(Starter(racer, bib))
    return starters


def guessed_start_list(model, tracker, disc, winter, names, size=60, exclude=()):
    """No start list yet: take racers active in the discipline last winter or this one,
    and give bibs in order of form, roughly how the seeding goes."""
    hmm = model.hmms[disc]
    recent = {k[0] for k, w in tracker.last_winter.items() if k[1] == disc and w >= winter - 1} - set(exclude)
    def strength(r):
        em = hmm.emit(tracker.predict(r, disc, winter))
        return em[WIN] * 3 + em[PODIUM] + em[TOP10] / 3
    ranked = sorted(recent, key=strength, reverse=True)[:size]
    if FAMILY[disc] == "speed":
        # speed seeding: the top 10 draw bibs 6-15, the next five get 1-5
        bibs = list(range(6, 16)) + list(range(1, 6)) + list(range(16, size + 1))
    else:
        bibs = list(range(1, size + 1))
    return [Starter(r, b) for r, b in zip(ranked, bibs)]


def cmd_predict(args):
    races, names, _ = load_history(args.db, args.extra_db)
    model = load_model(args, races)
    if args.race:
        con = sqlite3.connect(f"file:{args.race_db or args.db}?mode=ro", uri=True)
        row = con.execute("select short_name, kind, race_date from skitipp_raceevent where fis_id = ?", (args.race,)).fetchone()
        if not row:
            sys.exit(f"race {args.race} not found")
        name, kind, date = row
        if kind not in DISCIPLINES:
            sys.exit(f"{kind} isn't a discipline the bot tipps")
        starters = [Starter(r, b) for r, b in con.execute(
            "select racer_id, start_number from skitipp_racecompetitor where race_event_id = ?", (args.race,))]
        names.update(racer_names(con))
        date = parse_date(date)
        fis_id = args.race
    else:
        if not args.discipline:
            sys.exit("give --race or --discipline")
        kind, name, fis_id = KIND_OF[args.discipline], args.discipline, 0
        date = datetime.fromisoformat(args.date) if args.date else datetime.now()
        starters = read_start_list(args.start_list, names) if args.start_list else None

    if args.fis_race:
        # training runs straight from FIS, for a race that hasn't happened
        sys.path.insert(0, HERE)
        import fetch_extras
        _, runs = fetch_extras.race_extras(args.fis_race)
        trainings = [(fis_id, d, r["racer"], r["rank"], r["gap"], r["dnf"]) for d, r in runs]
    tracker = tracker_until(model, races, date)
    guessed = not starters
    race = Race(fis_id, name, kind, date, starters or [])
    if args.venue:
        race.venue = venue_key(args.venue)
    if args.fis_race:
        attach_training({fis_id: race}, trainings)
        if race.training:
            print(f"{len({d for _, d, *_ in trainings})} training runs from FIS", file=sys.stderr)
    if guessed:
        race.starters = guessed_start_list(model, tracker, race.disc, race.winter, names)
    boosts = {}
    find = racer_lookup(names)
    for b in args.boost:
        who, _, factor = b.rpartition("=")
        racer = find(who)
        if racer is None or not factor:
            sys.exit(f"--boost wants NAME=FACTOR, e.g. 'HAUGAN Timon=2', got {b!r}")
        boosts[racer] = float(factor)
    podium, dnf, exp, sim = tipp(model, tracker, race, boosts)

    print(f"{name} ({kind}) {date:%Y-%m-%d}" + ("  - no start list, bibs guessed from form" if guessed else ""))
    print(f"\n{'bib':>4} {'racer':28} {'x':>3} {'P(win)':>7} {'P(podium)':>10} {'P(dnf)':>7}")
    order = sorted(range(len(race.starters)), key=lambda i: -sim.p_podium[i])
    for i in order[:args.top]:
        s = race.starters[i]
        print(f"{s.bib:4} {names.get(s.racer, s.racer):28} {sim.mult[i]:3} {sim.p_rank[i][0]:7.1%}"
              f" {sim.p_podium[i]:10.1%} {sim.p_dnf[i]:7.1%}")
    print("\ntipp")
    for pos, r in enumerate(podium, 1):
        print(f"  {pos}. {names.get(r, r)}")
    print(f"  dnf: {names.get(dnf, dnf) if dnf else 'alle im Ziel'}")
    print(f"  expected points: {exp:.2f}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=DEFAULT_DB, help="history database (default tipbot/data/history.sqlite3)")
    p.add_argument("--extra-db", action="append",
                   help="more history scraped from FIS: older seasons, training runs (default tipbot/data/fis.sqlite3)")
    p.add_argument("--no-extra", action="store_true", help="use the history database alone")
    p.add_argument("--model", default=DEFAULT_MODEL)
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("backtest", help="train on earlier winters, tipp each later one")
    b.add_argument("--scheme", choices=["rolling", "loo"], default="rolling",
                   help="rolling: train on earlier winters only. loo: leave one winter out, train on all others")
    b.add_argument("--min-train", type=int, default=2, help="rolling: winters of history before the first test winter")
    b.add_argument("-v", "--verbose", action="store_true", help="print every tipp")
    sub.add_parser("train", help="fit on all history and save the model")
    f = sub.add_parser("form", help="current form per racer")
    f.add_argument("--discipline", choices=FAMILY, required=True)
    f.add_argument("--date")
    f.add_argument("--top", type=int, default=25)
    t = sub.add_parser("predict", help="tipp a race")
    t.add_argument("--race", type=int, help="FIS race id with a start list in --race-db")
    t.add_argument("--race-db", help="database holding the race's start list (default --db)")
    t.add_argument("--discipline", choices=FAMILY)
    t.add_argument("--start-list", help="csv of bib,racer where racer is a FIS code or name")
    t.add_argument("--date")
    t.add_argument("--fis-race", type=int, help="FIS race id: fetch the race's training runs from FIS")
    t.add_argument("--venue", help="venue for venue form, e.g. Kitzbuehel or Soelden")
    t.add_argument("--top", type=int, default=15)
    t.add_argument("--boost", action="append", default=[], metavar="NAME=FACTOR",
                   help="scale a racer's chances by what you know, e.g. 'HAUGAN Timon=2' or 'KRANJEC Zan=0.5'")

    args = p.parse_args()
    args.extra_db = [] if args.no_extra else (args.extra_db or [DEFAULT_EXTRA])
    {"backtest": cmd_backtest, "train": cmd_train, "form": cmd_form, "predict": cmd_predict}[args.command](args)


if __name__ == "__main__":
    main()
