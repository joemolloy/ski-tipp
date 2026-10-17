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
   podium rate and the start group give each starter a Plackett-Luce strength, fitted on past
   podiums. Podium chances follow exactly from those strengths. DNF chances come from the
   form model and start group, calibrated on past DNFs.
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
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(HERE, "data", "history.sqlite3")
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

# start groups, as in tipp_scorer.determine_start_group
GROUP_MULTIPLIER = [0, 1, 3, 6, 12]


def start_group(family, bib):
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


def multiplier(family, bib):
    return GROUP_MULTIPLIER[start_group(family, bib)]


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
    def __init__(self, fis_id, name, kind, date, starters, multiplier=1):
        self.fis_id, self.name, self.date = fis_id, name, date
        self.disc = DISCIPLINES[kind]
        self.family = FAMILY[self.disc]
        self.winter = winter_of(date)
        self.starters = starters
        self.multiplier = multiplier

    @property
    def finished(self):
        return any(s.rank == 1 for s in self.starters)


def parse_date(text):
    return datetime.fromisoformat(text[:19])


def racer_names(con):
    # result imports can append the ski brand on new lines, e.g. "GRAVIER Tiziano\n\n\nHead"
    return {fis_id: name.strip().split("\n")[0].strip() for fis_id, name in con.execute("select fis_id, name from skitipp_racer")}


def load_history(db_path):
    """Finished races in the four main disciplines, oldest first, plus racer names."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    names = racer_names(con)
    rows = con.execute(
        "select r.fis_id, r.short_name, r.kind, r.race_date, r.points_multiplier, "
        "c.racer_id, c.start_number, c.rank, c.is_dnf "
        "from skitipp_raceevent r join skitipp_racecompetitor c on c.race_event_id = r.fis_id "
        "where r.cancelled = 0 order by r.race_date, r.fis_id"
    )
    races = {}
    for fis_id, name, kind, date, mult, racer, bib, rank, dnf in rows:
        if kind not in DISCIPLINES:
            continue
        if fis_id not in races:
            races[fis_id] = Race(fis_id, name, kind, parse_date(date), [], mult or 1)
        races[fis_id].starters.append(Starter(racer, bib, rank, bool(dnf)))

    human = defaultdict(dict)
    for race_id, tipper, pts in con.execute(
        "select p.race_event_id, u.username, p.standard_points + p.bonus_points "
        "from skitipp_tipppointtally p join auth_user u on u.id = p.tipper_id where p.tipp_id is not null"
    ):
        human[race_id][tipper] = pts
    con.close()
    finished = [r for r in races.values() if r.finished]
    finished.sort(key=lambda r: r.date)
    return finished, names, human


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

    def observe(self, race):
        present = set()
        for s in race.starters:
            self._observe(s.racer, race, outcome(s.rank, s.dnf))
            present.add(s.racer)
        # racers who have raced this discipline this winter but aren't in the results: "out"
        seen = self.seen_this_winter[(race.disc, race.winter)]
        for racer in seen - present:
            self._observe(racer, race, OUT)
        seen |= present

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


def form_sequences(races):
    """Per (racer, discipline) observation sequences, matching FormTracker.observe."""
    seqs = defaultdict(lambda: ([], []))
    last_winter = {}
    seen = defaultdict(set)
    for race in races:
        present = {}
        for s in race.starters:
            present[s.racer] = outcome(s.rank, s.dnf)
        for racer in seen[(race.disc, race.winter)] - set(present):
            present[racer] = OUT
        for racer, obs in present.items():
            key = (racer, race.disc)
            o, g = seqs[key]
            g.append(NEW_WINTER if key in last_winter and last_winter[key] != race.winter else 0)
            o.append(obs)
            last_winter[key] = race.winter
        seen[(race.disc, race.winter)] |= set(present)
    by_disc = defaultdict(list)
    for (racer, disc), seq in seqs.items():
        by_disc[disc].append(seq)
    return by_disc


# ---------------------------------------------------------------- race model

def logit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def features(family, emission, sibling, record, bib):
    """Plackett-Luce features for one starter. Start groups are one-hot, group 1 is the baseline."""
    p_win = emission[WIN]
    p_pod = p_win + emission[PODIUM]
    p_top10 = p_pod + emission[TOP10]
    p_top30 = p_top10 + emission[TOP30]
    groups = [0.0, 0.0, 0.0]
    g = start_group(family, bib)
    if g > 1:
        groups[g - 2] = 1.0
    sib = math.log(sibling[WIN] + sibling[PODIUM] + sibling[TOP10] + 1e-3)
    own = [math.log(record[0] + 1e-4), math.log(record[1] + 1e-4)]
    return [math.log(p_win + 1e-4), math.log(p_pod + 1e-4), math.log(p_top10 + 1e-3), math.log(p_top30 + 1e-3), sib] + own + groups


FEATURE_NAMES = ["log P(win)", "log P(podium)", "log P(top10)", "log P(top30)", "sister discipline log P(top10)", "own log win rate", "own log podium rate", "start group 2", "start group 3", "start group 4"]


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
    """samples: list of (X, order) with X the finishers' features and order their finishing order."""
    w = [0.0] * n_features
    w[0] = 1.0
    for _ in range(iterations):
        grad = [-ridge * x for x in w]
        H = [[ridge if i == j else 0.0 for j in range(n_features)] for i in range(n_features)]
        for X, order in samples:
            ex = [math.exp(sum(a * b for a, b in zip(w, x))) for x in X]
            remaining = set(range(len(X)))
            for k in order[:top]:
                total = sum(ex[j] for j in remaining)
                mean = [sum(ex[j] * X[j][f] for j in remaining) / total for f in range(n_features)]
                for f in range(n_features):
                    grad[f] += X[k][f] - mean[f]
                for j in remaining:
                    pj = ex[j] / total
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


def fit_logistic(X, ys, ridge=1.0, iterations=15):
    """Logistic regression by Newton's method, for DNF calibration."""
    n = len(X[0])
    w = [0.0] * n
    for _ in range(iterations):
        grad = [-ridge * a for a in w]
        H = [[ridge if i == j else 0.0 for j in range(n)] for i in range(n)]
        for x, y in zip(X, ys):
            p = 1 / (1 + math.exp(-sum(a * b for a, b in zip(w, x))))
            v = p * (1 - p)
            for i in range(n):
                grad[i] += (y - p) * x[i]
                for j in range(n):
                    H[i][j] += v * x[i] * x[j]
        step = solve(H, grad)
        w = [a + b for a, b in zip(w, step)]
        if max(abs(a) for a in step) < 1e-5:
            break
    return w


def dnf_features(family, emission, bib):
    # DNF depends on form but also on the start group: late starters race on a rutted course
    groups = [0.0, 0.0, 0.0]
    g = start_group(family, bib)
    if g > 1:
        groups[g - 2] = 1.0
    return [1.0, logit(emission[DNF])] + groups


class RaceModel:
    def __init__(self, hmms, weights, dnf_calibration, late_podium_rate):
        self.hmms = hmms
        self.weights = weights            # family -> PL weights
        self.dnf_calibration = dnf_calibration  # family -> logistic weights
        self.late_podium_rate = late_podium_rate  # family -> podium places per race won from bib 31+

    def to_json(self):
        return dict(
            hmms={d: h.to_json() for d, h in self.hmms.items()},
            weights=self.weights,
            dnf_calibration=self.dnf_calibration,
            late_podium_rate=self.late_podium_rate,
        )

    @classmethod
    def from_json(cls, d):
        return cls({k: FormHMM.from_json(v) for k, v in d["hmms"].items()}, d["weights"],
                   d["dnf_calibration"], d["late_podium_rate"])

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
        for race in races:
            family_races[race.family] += 1
            late_podiums[race.family] += sum(1 for s in race.starters if s.bib > 30 and not s.dnf and s.rank and s.rank <= 3)
            # older databases only kept DNFs from bib 30 and better, which would skew the DNF model
            dnf_complete = race.family == "speed" or any(s.dnf and s.bib > 30 for s in race.starters)
            # Plackett-Luce over every starter, DNFs included, so DNF risk is part of the strength
            X = []
            for s in race.starters:
                em = tracker.hmms[race.disc].emit(tracker.predict(s.racer, race.disc, race.winter))
                X.append(features(race.family, em, tracker.emit_sibling(s.racer, race), tracker.own_record(s.racer, race.disc, em), s.bib))
                dnf_X[race.family].append((dnf_complete, dnf_features(race.family, em, s.bib)))
                dnf_ys[race.family].append((dnf_complete, 1.0 if s.dnf else 0.0))
            finishers = [i for i, s in enumerate(race.starters) if not s.dnf and s.rank]
            order = sorted(finishers, key=lambda i: race.starters[i].rank)
            pl_samples[race.family].append((X, order))
            tracker.observe(race)

        weights, calibration = {}, {}
        for family in ("tech", "speed"):
            log(f"  fitting race model for {family} on {len(pl_samples[family])} races")
            weights[family] = fit_plackett_luce(pl_samples[family], len(FEATURE_NAMES), top=PL_TOP)
            use_all = not any(c for c, _ in dnf_X[family])  # no complete records: take what there is
            X = [x for c, x in dnf_X[family] if c or use_all]
            y = [v for c, v in dnf_ys[family] if c or use_all]
            calibration[family] = fit_logistic(X, y)
        late_rate = {f: late_podiums[f] / max(family_races[f], 1) for f in family_races}
        return cls(hmms, weights, calibration, late_rate)

    def starter_probabilities(self, tracker, race):
        """Per starter: (PL strength, DNF probability, form emission)."""
        w = self.weights[race.family]
        wd = self.dnf_calibration[race.family]
        out = []
        for s in race.starters:
            em = self.hmms[race.disc].emit(tracker.predict(s.racer, race.disc, race.winter))
            x = features(race.family, em, tracker.emit_sibling(s.racer, race), tracker.own_record(s.racer, race.disc, em), s.bib)
            u = sum(a * b for a, b in zip(w, x))
            x = dnf_features(race.family, em, s.bib)
            p_dnf = 1 / (1 + math.exp(-sum(a * b for a, b in zip(wd, x))))
            out.append((u, p_dnf, em))
        return out

    def odds(self, tracker, race):
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
        return RaceOdds(race, probs)


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
        self.mult = [multiplier(race.family, st.bib) for st in race.starters]
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
        e += self.p_rank[a][0] + 0.5 * self.p_rank[b][1] + 0.5 * self.p_rank[c][2]
        # podium bonus: 0.5 for exactly two right, 1 for three = 0.5 * sum of pairs - 0.5 * triple
        return e + 0.5 * (self.pair(a, b) + self.pair(a, c) + self.pair(b, c)) - 0.5 * self.triple(a, b, c)

    def best_podium(self, candidates=14):
        value = lambda i: self.mult[i] * self.p_podium[i] + self.p_rank[i][0]
        pool = sorted(range(len(self.mult)), key=value, reverse=True)[:candidates]
        best = max(
            ((a, b, c) for a in pool for b in pool for c in pool if len({a, b, c}) == 3),
            key=lambda t: self.expected_points(*t),
        )
        return best, self.expected_points(*best)

    def favourites(self):
        """Baseline: the three most likely podium finishers, ordered by win chance."""
        top = sorted(range(len(self.mult)), key=lambda i: self.p_podium[i], reverse=True)[:3]
        return tuple(sorted(top, key=lambda i: -self.p_rank[i][0]))

    def best_dnf(self):
        """(starter index or None for alle im Ziel, expected DNF points)."""
        i = max(range(len(self.p_dnf)), key=lambda i: self.p_dnf[i])
        if 0.5 * self.p_all_in > self.p_dnf[i]:
            return None, 0.5 * self.p_all_in
        return i, self.p_dnf[i]


def score(race, podium_racers, dnf_racer):
    """Ski-tipp points (standard + bonus, before multiplier and alleinsieger) of a tipp."""
    result = {s.racer: s for s in race.starters}
    pts, on, rank_bonus = 0.0, 0, 0.0
    for pos, racer in enumerate(podium_racers, 1):
        s = result.get(racer)
        if s and not s.dnf and s.rank and s.rank <= 3:
            pts += multiplier(race.family, s.bib)
            on += 1
            if s.rank == pos:
                rank_bonus += 1 if pos == 1 else 0.5
    pts += rank_bonus + {2: 0.5, 3: 1}.get(on, 0)
    if dnf_racer is None:
        if not any(s.dnf and s.bib <= 30 for s in race.starters):
            pts += 0.5
    elif result.get(dnf_racer) and result[dnf_racer].dnf:
        pts += 1
    return pts


def tipp(model, tracker, race):
    sim = model.odds(tracker, race)
    (a, b, c), e_pod = sim.best_podium()
    dnf_i, e_dnf = sim.best_dnf()
    racers = [race.starters[i].racer for i in (a, b, c)]
    dnf = None if dnf_i is None else race.starters[dnf_i].racer
    return racers, dnf, e_pod + e_dnf, sim


# ---------------------------------------------------------------- commands

def cmd_backtest(args):
    races, names, human = load_history(args.db)
    winters = sorted({r.winter for r in races})
    test_winters = [w for w in winters if w >= winters[0] + args.min_train]
    print(f"{len(races)} finished races across winters {winter_name(winters[0])}-{winter_name(winters[-1])}\n")
    rows = []
    for w in test_winters:
        train = [r for r in races if r.winter < w]
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


def cmd_train(args):
    races, _, _ = load_history(args.db)
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


def tracker_until(model, races, date):
    tracker = FormTracker(model.hmms)
    for race in races:
        if race.date < date:
            tracker.observe(race)
    return tracker


def cmd_form(args):
    races, names, _ = load_history(args.db)
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


def read_start_list(path, names):
    by_name = {}
    for fis_id, name in names.items():
        by_name[name.lower()] = fis_id
        by_name.setdefault(name.split(" ")[0].lower(), fis_id)
    starters = []
    with open(path) as f:
        for row in csv.reader(f):
            if not row or not row[0].strip().isdigit():
                continue
            bib, who = int(row[0]), row[1].strip()
            racer = int(who) if who.isdigit() else by_name.get(who.lower())
            if racer is None:
                sys.exit(f"unknown racer {who!r} in {path}, use the FIS code instead")
            starters.append(Starter(racer, bib))
    return starters


def guessed_start_list(model, tracker, disc, winter, names, size=60):
    """No start list yet: take racers active in the discipline last winter or this one,
    and give bibs in order of form, roughly how the seeding goes."""
    hmm = model.hmms[disc]
    recent = {k[0] for k, w in tracker.last_winter.items() if k[1] == disc and w >= winter - 1}
    def strength(r):
        em = hmm.emit(tracker.predict(r, disc, winter))
        return em[WIN] * 3 + em[PODIUM] + em[TOP10] / 3
    ranked = sorted(recent, key=strength, reverse=True)[:size]
    return [Starter(r, i + 1) for i, r in enumerate(ranked)]


def cmd_predict(args):
    races, names, _ = load_history(args.db)
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

    tracker = tracker_until(model, races, date)
    guessed = not starters
    race = Race(fis_id, name, kind, date, starters or [])
    if guessed:
        race.starters = guessed_start_list(model, tracker, race.disc, race.winter, names)
    podium, dnf, exp, sim = tipp(model, tracker, race)

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
    p.add_argument("--model", default=DEFAULT_MODEL)
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("backtest", help="train on earlier winters, tipp each later one")
    b.add_argument("--min-train", type=int, default=2, help="winters of history before the first test winter")
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
    t.add_argument("--top", type=int, default=15)

    args = p.parse_args()
    {"backtest": cmd_backtest, "train": cmd_train, "form": cmd_form, "predict": cmd_predict}[args.command](args)


if __name__ == "__main__":
    main()
