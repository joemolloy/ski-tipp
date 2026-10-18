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


@tipp_assist_only
def tipp_assist_page(request):
    n = max(1, min(int(request.GET.get("n", 6)), 20))
    state = load_state()
    races = upcoming_races(n)
    upcoming = [dict(fis_id=r.fis_id, kind=r.kind, date=naive_utc(r.race_date), name=r.short_name, location=r.location,
                     starters=list(r.start_list.values_list("racer_id", "start_number")))
                for r in races]
    eligible = set(Racer.objects.competitors().values_list("fis_id", flat=True))
    trainings = {int(k): v for k, v in state.get("training", {}).items()}
    results, names = markov_tipper.tipps_for(
        upcoming, settings.DATABASES["default"]["NAME"], [EXTRA], exclude=state.get("not_starting", []),
        eligible=eligible, trainings=trainings, now=naive_utc(timezone.now()))
    by_id = {r["fis_id"]: r for r in results}
    racer_names = {**names, **dict(Racer.objects.values_list("fis_id", "name"))}
    label = lambda fis_id: (racer_names.get(fis_id) or str(fis_id)).split("\n")[0]

    cards = []
    for race in races:
        res = by_id.get(race.fis_id)
        if not res:
            continue
        mine = (race.tipps.filter(tipper=request.user).order_by("-created").first())
        cards.append(dict(
            race=race,
            podium=[dict(fis_id=r, name=label(r), bib=res["bib"].get(r), mult=res["mult"].get(r)) for r in res["podium"]],
            dnf=dict(fis_id=res["dnf"] or 0, name=label(res["dnf"]) if res["dnf"] else "Alle im Ziel",
                     p=res["p_dnf"].get(res["dnf"]) if res["dnf"] else res["p_all_in"]),
            expected=res["expected"], guessed_bibs=res["guessed_bibs"], training_runs=res["training_runs"],
            contenders=[dict(c, name=label(c["racer"])) for c in res["contenders"]],
            mine=mine, is_speed=race.is_speed_event,
            # races without a published start time are stored at local midnight
            time_known=timezone.localtime(race.race_date).strftime("%H:%M") != "00:00",
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
    """Fetch the training runs of the coming speed races from FIS, then show the page again."""
    if request.method != "POST":
        return back(request)
    from tipbot import fetch_extras
    from skitipp.fis_connector import FISRateLimited
    fetch_extras.PAUSE = 1
    state = load_state()
    state.setdefault("training", {}); state.setdefault("training_fetched", {})
    soon = timezone.now() + timezone.timedelta(days=8)
    fetched = []
    for race in upcoming_races(20):
        if not race.is_speed_event or race.race_date > soon:
            continue
        try:
            _, runs = fetch_extras.race_extras(race.fis_id)
        except FISRateLimited:
            messages.warning(request, "FIS is rate limiting, try again in a minute.")
            break
        except Exception as e:
            messages.warning(request, f"Couldn't load training runs for {race}: {e}")
            continue
        state["training"][str(race.fis_id)] = [(d, r["racer"], r["rank"], r["gap"], r["dnf"]) for d, r in runs]
        state["training_fetched"][str(race.fis_id)] = timezone.now().isoformat(timespec="minutes")
        fetched.append(f"{race} ({len({d for d, _ in runs})} runs)")
    save_state(state)
    messages.info(request, "Training runs: " + (", ".join(fetched) if fetched else "no speed races in the next week"))
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
                        place_3_id=picks[2], dnf_id=dnf if race.dnf_eligible else None, comment="tipp-assist")
    messages.success(request, f"Tipp saved for {race}.")
    return back(request)
