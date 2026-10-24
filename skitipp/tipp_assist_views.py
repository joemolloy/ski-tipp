"""Tipp-Assist: the tipbot's tipps for the next races, for members of the Tipp-Assist group."""
import json
import os
from datetime import timezone as dt_timezone

from dal import autocomplete
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from tipbot import markov_tipper

from .context_processors import can_use_tipp_assist
from .models import RACE_TYPES, Racer, RaceEvent, Season, Tipp

DATA = settings.TIPP_ASSIST_DATA
STATE = os.path.join(DATA, "tipp_assist_state.json")
EXTRA = os.path.join(DATA, "fis.sqlite3")


class NotStartingForm(forms.Form):
    racers = forms.ModelMultipleChoiceField(
        Racer.objects.all(), required=False, label="Not starting",
        widget=autocomplete.ModelSelect2Multiple(url="racer-autocomplete", attrs={"data-placeholder": "Injured or not racing"}),
    )


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"not_starting": [], "training": {}}


def save_state(state):
    os.makedirs(DATA, exist_ok=True)
    with open(STATE + ".tmp", "w") as f:
        json.dump(state, f)
    os.replace(STATE + ".tmp", STATE)


def tipp_assist_only(view):
    @login_required
    def wrapped(request, *args, **kwargs):
        if not can_use_tipp_assist(request.user):
            raise Http404
        return view(request, *args, **kwargs)
    return wrapped


def back(request):
    """Back to the page, keeping the number of races shown."""
    n = request.POST.get("n", "6")
    return redirect(f"{reverse('tipp_assist')}?n={int(n) if n.isdigit() else 6}")


def naive_utc(dt):
    return dt.astimezone(dt_timezone.utc).replace(tzinfo=None)


def upcoming_races(n):
    kinds = [k for k, v in RACE_TYPES.items() if v != "other" and k in markov_tipper.DISCIPLINES]
    return list(RaceEvent.objects.filter(race_date__gt=timezone.now(), cancelled=False, kind__in=kinds)
                .order_by("race_date")[:n])


def assist_tipps(races, user):
    """The tipbot's tipps for these races, with the expected points of the user's latest tipp.
    Returns the results by race id, the user's tipps by race id, and a racer label function."""
    state = load_state()
    upcoming = [dict(fis_id=r.fis_id, kind=r.kind, date=naive_utc(r.race_date), name=r.short_name, location=r.location,
                     starters=list(r.start_list.values_list("racer_id", "start_number")))
                for r in races]
    # the user's latest tipp per race; dnf 0 in the app means alle im Ziel
    mine = {r.fis_id: r.tipps.filter(tipper=user).order_by("-created").first() for r in races}
    current = {fid: ([t.place_1_id, t.place_2_id, t.place_3_id], t.dnf_id or None) for fid, t in mine.items() if t}
    eligible = set(Racer.objects.competitors().values_list("fis_id", flat=True))
    trainings = {int(k): v for k, v in state.get("training", {}).items()}
    results, names = markov_tipper.tipps_for(
        upcoming, settings.DATABASES["default"]["NAME"], [EXTRA], exclude=state.get("not_starting", []),
        eligible=eligible, trainings=trainings, now=naive_utc(timezone.now()), current=current)
    racer_names = {**names, **dict(Racer.objects.values_list("fis_id", "name"))}
    label = lambda fis_id: (racer_names.get(fis_id) or str(fis_id)).split("\n")[0]
    return {r["fis_id"]: r for r in results}, mine, label


@tipp_assist_only
def tipp_assist_page(request):
    n = max(1, min(int(request.GET.get("n", 6)), 20))
    state = load_state()
    races = upcoming_races(n)
    by_id, mine, label = assist_tipps(races, request.user)

    cards = []
    for race in races:
        res = by_id.get(race.fis_id)
        if not res:
            continue
        podium = lambda racers: [dict(fis_id=r, name=label(r), bib=res["bib"].get(r), mult=res["mult"].get(r)) for r in racers]
        dnf = lambda r: dict(fis_id=r or 0, name=label(r) if r else "Alle im Ziel",
                             p=res["p_dnf"].get(r, 0.0) if r else res["p_all_in"])
        cur = res.get("current")
        # tabs: the user's own tipp first if it isn't one of the suggestions, then the suggestions
        tabs = [dict(label="Yours", expected=o["expected"], podium=podium(o["podium"]), dnf=dnf(res["dnf"]),
                     chosen=bool(cur) and cur["option"] == k) for k, o in enumerate(res["options"])]
        for k, t in enumerate(tabs):
            t["label"] = "Best" if k == 0 else f"#{k + 1}"
        if cur and cur["option"] is None:
            tabs.insert(0, dict(label="Yours", expected=cur["expected"], podium=podium(cur["podium"]), dnf=dnf(cur["dnf"]),
                                chosen=True))
        active = next((k for k, t in enumerate(tabs) if t["chosen"]), 0)
        for k, t in enumerate(tabs):
            t["active"] = k == active
            t["id"] = f"opt-{race.fis_id}-{k}"
        cards.append(dict(
            race=race, tabs=tabs, mine=mine.get(race.fis_id),
            guessed_bibs=res["guessed_bibs"], training_runs=res["training_runs"],
            contenders=[dict(c, name=label(c["racer"])) for c in res["contenders"]],
            is_speed=race.is_speed_event,
            # races without a published start time are stored at 9:00 (a real 9:00 start shows as tbc too)
            time_known=timezone.localtime(race.race_date).strftime("%H:%M") != "09:00",
        ))
    form = NotStartingForm(initial={"racers": Racer.objects.filter(fis_id__in=state.get("not_starting", []))})
    return render(request, "tipp_assist.html", dict(cards=cards, form=form, n=n,
                                                    training_fetched=state.get("training_fetched", {}),
                                                    selected_season=Season.objects.order_by("-current", "-start_date").first()))


@tipp_assist_only
def tipp_assist_not_starting(request):
    if request.method == "POST":
        form = NotStartingForm(request.POST)
        if form.is_valid():
            state = load_state()
            state["not_starting"] = [r.fis_id for r in form.cleaned_data["racers"]]
            save_state(state)
            messages.info(request, "Not starting: " + (", ".join(r.name.split("\n")[0] for r in form.cleaned_data["racers"]) or "nobody"))
    return back(request)


@tipp_assist_only
def tipp_assist_rerun(request):
    """Refresh the coming week's races from FIS (start time and start list), fetch the training runs
    of its speed races, then show the page again."""
    if request.method != "POST":
        return back(request)
    from tipbot import fetch_extras
    from skitipp import fis_connector
    fetch_extras.PAUSE = 1
    state = load_state()
    state.setdefault("training", {}); state.setdefault("training_fetched", {})
    soon = timezone.now() + timezone.timedelta(days=8)
    listed, fetched = [], []
    for race in upcoming_races(20):
        if race.race_date > soon:
            continue
        try:
            race = fis_connector.get_race_results(race.fis_id)
            starters = race.start_list.count()
            listed.append(f"{race} ({starters} starters)" if starters else f"{race} (not out yet)")
            if race.is_speed_event:
                _, runs = fetch_extras.race_extras(race.fis_id)
                state["training"][str(race.fis_id)] = [(d, r["racer"], r["rank"], r["gap"], r["dnf"]) for d, r in runs]
                state["training_fetched"][str(race.fis_id)] = timezone.now().isoformat(timespec="minutes")
                fetched.append(f"{race} ({len({d for d, _ in runs})} runs)")
        except fis_connector.FISRateLimited:
            messages.warning(request, "FIS is rate limiting, try again in a minute.")
            break
        except Exception as e:
            messages.warning(request, f"Couldn't load {race} from FIS: {e}")
            continue
    save_state(state)
    messages.info(request, "Start lists: " + (", ".join(listed) if listed else "no races in the next week"))
    if fetched:
        messages.info(request, "Training runs: " + ", ".join(fetched))
    return back(request)


@tipp_assist_only
def tipp_assist_use(request, race_id):
    """Submit the bot's tipp as the logged-in user's tipp."""
    if request.method != "POST":
        return back(request)
    race = get_object_or_404(RaceEvent, pk=race_id)
    if race.start_date_in_past:
        messages.warning(request, f"{race} has already started, the tipp wouldn't count.")
        return back(request)
    try:
        picks = [int(request.POST[k]) for k in ("place_1", "place_2", "place_3")]
        dnf = int(request.POST.get("dnf", 0))
    except (KeyError, ValueError):
        raise Http404
    competitors = set(Racer.objects.competitors().filter(fis_id__in=picks).values_list("fis_id", flat=True))
    if len(set(picks)) != 3 or competitors != set(picks):
        messages.warning(request, "Those racers can't be picked on the tipp form.")
        return back(request)
    if race.dnf_eligible and not Racer.objects.dnf_list().filter(fis_id=dnf).exists():
        messages.warning(request, "That DNF racer can't be picked on the tipp form.")
        return back(request)
    Tipp.objects.create(tipper=request.user, race_event=race, place_1_id=picks[0], place_2_id=picks[1],
                        place_3_id=picks[2], dnf_id=dnf if race.dnf_eligible else None)
    messages.success(request, f"Tipp saved for {race}.")
    return back(request)
