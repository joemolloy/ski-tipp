from django.shortcuts import render, get_object_or_404, redirect
from django import http
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages

from django.db import models
from django.db.models import BooleanField
from django.db.models import Exists, OuterRef, Sum, Count, Case, CharField, Value, When, Q, F, Subquery, Value
from django.db.models.functions import Coalesce

import datetime

from django.http import HttpResponseRedirect
from django.urls import reverse

from dal import autocomplete

from django.views.generic.edit import CreateView, UpdateView, DeleteView
from django.views.generic.detail import DetailView
from django.views.generic.list import ListView
from django.views.generic.base import TemplateView

from skitipp.forms import *
from skitipp.models import RaceEvent, Racer, TippPointTally, PointAdjustment

from django.contrib.auth.models import User
from django.forms.models import model_to_dict

from skitipp import tipp_scorer

import logging
from operator import itemgetter

class RacerAutocomplete(LoginRequiredMixin, autocomplete.Select2QuerySetView):
    def get_queryset(self):
        field = self.forwarded.get('racer_type', None)
        if field == 'dnf':
            qs = Racer.objects.all().annotate(
                is_dnf=Case(When(fis_id=0, then=Value(True)), default=Value(False), output_field=BooleanField())
            ).order_by('-is_dnf', 'name')
        else:
            qs = Racer.objects.competitors().order_by('name')

        if self.q:
            qs = qs.filter(name__icontains=self.q)

        return qs

class RaceListView(LoginRequiredMixin, ListView):
    template_name = 'race_list.html'
    model = RaceEvent

    def get_queryset(self):

        #messages.success(self.request, "Welcome to ski-tipp!")

        #annotate if tipp was made for this race
        valid_tipp = Tipp.objects.filter(
            race_event=OuterRef('pk'),
            tipper=self.request.user,
            #created_at__gte=one_day_ago,
        )
        return RaceEvent.objects.all().annotate(
            user_has_tipped=Exists(valid_tipp),
            race_status=Case(
                When(Q(in_progress=True) | Q(race_date__date__gte = datetime.date.today()), then=Value('Upcoming & In Progress')),
                default=Value('Finished'),
                output_field=CharField(),
            )
        ).order_by('-race_status', 'race_date')


    def get_context_data(self, **kwargs):
        context = super(RaceListView, self).get_context_data(**kwargs)
        context['race_list'] = 'active'
        return context

class RaceEventCreateView(LoginRequiredMixin, CreateView):
    template_name = 'race_event_form.html'
    form_class = RaceEventForm

class RaceEventEditView(LoginRequiredMixin, UpdateView):
    template_name = 'race_event_form.html'
    model = RaceEvent
    form_class = RaceEventForm

class RaceEventDeleteView(LoginRequiredMixin, DeleteView):
    template_name = 'race_event_confirm_delete.html'
    model = RaceEvent
    success_url = '/app/racelist/'


class RaceEventDetailView(LoginRequiredMixin, DetailView):
    template_name = 'race_event_detail.html'
    model = RaceEvent

    def get_context_data(self, **kwargs):
        context = super(RaceEventDetailView, self).get_context_data(**kwargs)
        tipps = Tipp.objects.filter(
            tipper=self.request.user, race_event_id=self.kwargs['pk']).annotate(
            is_best_tipp=F('tipp_points_tally__is_best_tipp')
        ).order_by('-created')[:1]

        if len(tipps):
            context['current_tipp'] = tipps[0]

        return context

class RaceResultsView(LoginRequiredMixin, DetailView):
    template_name = 'race_event_results.html'
    model = RaceEvent



class TippCreateView(LoginRequiredMixin, CreateView):
    template_name = 'tipp_create_form.html'
    form_class = TippForm
    
    def get_context_data(self, **kwargs):
        context = super(TippCreateView, self).get_context_data(**kwargs)
        context['race_event'] = get_object_or_404(RaceEvent, pk=self.kwargs['race_id'])

        return context

    def get_form_kwargs(self):
        kwargs = super(TippCreateView, self).get_form_kwargs()
        kwargs['initial']['race_event'] = RaceEvent.objects.get(pk=self.kwargs['race_id'])
        kwargs['initial']['tipper'] = self.request.user

        return kwargs

    def get_success_url(self):
        return reverse('race_detail', args = (self.object.race_event.fis_id,))

class ManualTippView(LoginRequiredMixin, CreateView):
    template_name = 'tipp_create_form.html'
    form_class = TippForm
    
    def get_context_data(self, **kwargs):
        context = super(ManualTippView, self).get_context_data(**kwargs)
        context['race_event'] = get_object_or_404(RaceEvent, pk=self.kwargs['race_id'])

        return context

    def get_form_kwargs(self):
        kwargs = super(ManualTippView, self).get_form_kwargs()
        kwargs['initial']['race_event'] = RaceEvent.objects.get(pk=self.kwargs['race_id'])
        kwargs['initial']['tipper'] = get_object_or_404(User, username=self.kwargs['tipper'])
        kwargs['initial']['corrected_tipp'] = True
        
        return kwargs


from collections import defaultdict, OrderedDict

@login_required
def leaderboardDataView(request):

    best_tips = TippPointTally.objects.filter(race_event=OuterRef('pk')).filter(is_best_tipp=True)

    all_races = RaceEvent.objects.all().annotate(
        alleine=Subquery(best_tips.values('tipper__username')[:1])
    ).order_by('race_date')

    ranked_users = User.objects.all().annotate(
        preseason_adj=Coalesce(Sum('points_adjustments__points', filter=Q(points_adjustments__preseason=True)), Value(0)),
        season_adj=Coalesce(Sum('points_adjustments__points', filter=Q(points_adjustments__preseason=False)), Value(0)),
    ).values('id', 'username', 'preseason_adj', 'season_adj')

    #tally up the points for the race for each active user

    race_list =  list(all_races.values())
    leaderboard = []

    for u in ranked_users:
        u['races'] = []
        total_race_points = 0

        for race in all_races:
            race_points = None
            did_tipp = None
            race_tipp_score = TippPointTally.objects.filter(tipper_id=u['id'], race_event=race).first()
            if race_tipp_score:
                race_points = race_tipp_score.total_points
                total_race_points += race_points
                did_tipp = race_tipp_score.did_tipp
                #total_points = int(total_points) if total_points.is_integer() else total_points
            else:
                race_points = None
            
            u['races'].append({'points': race_points, 'did_tipp': did_tipp, 'fis_id': race.fis_id })

        u['race_total'] = total_race_points
        u['total'] = u['race_total'] + u['preseason_adj'] + u['season_adj']

        leaderboard.append(u)

    #rank userboard
    leaderboard.sort(key=itemgetter('total'), reverse=True)

    score_previous_rank = None
    current_rank = 0
    for u in leaderboard:
        user_score = u['total']
        if score_previous_rank is None or user_score < score_previous_rank:
            current_rank += 1
            score_previous_rank = user_score
        u['rank'] = current_rank

    data = { "races" : race_list, "tippers" : leaderboard }

    return http.JsonResponse(data, json_dumps_params={'indent': 4} )


class LeaderboardView(LoginRequiredMixin, TemplateView):
    template_name = "leaderboard.html"

    def get_context_data(self, **kwargs):
        context = super(LeaderboardView, self).get_context_data(**kwargs)
        context['leaderboard'] = 'active'
        return context




from skitipp import fis_connector

@staff_member_required
def upload_race(request):
    # if this is a POST request we need to process the form data
    if request.method == 'POST':
        # create a form instance and populate it with data from the request:
        form = UploadRaceForm(request.POST)
        # check whether it's valid:

        if form.is_valid():
            # process the data in form.cleaned_data as required
            race_fis_id = form.cleaned_data['fis_id']

            fis_connector.get_race_results(race_fis_id)

            # redirect to a new URL:
            #return HttpResponseRedirect(reverse('race_detail', kwargs={'pk': race_fis_id}))
            return HttpResponseRedirect(reverse('race_list'))

    # if a GET (or any other method) we'll create a blank form
    else:
        form = UploadRaceForm()

    return render(request, 'upload_race_form.html', {'form': form})

@staff_member_required
def update_race(request, race_id):
    race_event = fis_connector.get_race_results(race_id)
    return HttpResponseRedirect(race_event.get_absolute_url())


def publish_tipps(request, race_id):
    race_event = get_object_or_404(RaceEvent, pk=race_id)

    race_event = fis_connector.get_race_results(race_id)

    if not race_event.start_date_in_past:
        messages.warning(request, "%s not yet started. Tipps not published" % race_event)
        return HttpResponseRedirect(race_event.get_absolute_url())

    race_event.in_progress = True
    race_event.finished = False
    race_event.save()

    messages.info(request, "Tipps for %s have been published, tipping is now closed." % race_event)
    return HttpResponseRedirect(race_event.get_absolute_url())


@staff_member_required
def update_wc_start_list(request):
    fis_connector.update_ws_start_list()
    messages.info(request, "Racers updated from FIS WC Startlist")
    return HttpResponseRedirect(reverse('race_list'))

@staff_member_required
def rescore_all_races(request):

    TippPointTally.objects.all().delete()

    for race_event in RaceEvent.objects.filter(finished=True).order_by('race_date'):
        
        race_event = fis_connector.get_race_results(race_event.fis_id)

        if not race_event.start_list.exists():
            #race results are not published
            messages.error(request, "Race Results are not available for {}".format(race_event))
        else:
            tipp_scorer.score_race(race_event)
    
    return redirect(reverse('leaderboard'))



@staff_member_required
def finalize_race(request, race_id):

    #update race first
    race_event = fis_connector.get_race_results(race_id)

    if not race_event.start_list.exists():
        #race results are not published
        messages.error(request, "Race Results are not available to finalize race")
        return HttpResponseRedirect(race_event.get_absolute_url())

    #results exist, continue with finalizing
    race_event.in_progress = False
    race_event.finished = True
    race_event.save(update_fields=['finished', 'in_progress'])

    print("finializing race {}".format(race_event))
    #delete points from this race
    race_event.race_points_tally.all().delete()
    #score race
    tipp_scorer.score_race(race_event)
    
    messages.success(request, "{} was finalized".format(race_event))
    return HttpResponseRedirect(race_event.get_absolute_url())

class PointAdjustmentListView(CreateView):
    
    form_class = PointAdjustmentForm
    template_name = "point_adjustments.html"

    def get_context_data(self, **kwargs):
        context = super(PointAdjustmentListView, self).get_context_data(**kwargs)
        context['point_adjustment_list'] = PointAdjustment.objects.all().annotate(
            sign=Case(
                When(points__gt=0, then=Value('positive')),
                default=Value('negative'),
                output_field=CharField(),
            )
        ).order_by('-created')
        
        context['point_adjustments'] = 'active'

        return context

@staff_member_required
def deletePointAdjustment(request, adjustment_id):
    get_object_or_404(PointAdjustment, pk=adjustment_id).delete()
    return redirect('point_adjustments')

class AboutView(TemplateView):
    template_name = "about.html"

from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import logout, authenticate, login

from django.contrib import auth

def login_view(request):

    form = CustomLoginForm

    if request.user.is_authenticated:
        return redirect('index')
    
    if request.method == "POST":

        logging.info(request.POST)

        # here you get the post request username and password
        username = request.POST.get('username', '')
        password = request.POST.get('password', '')

        # authentication of the user, to check if it's active or None
        user = auth.authenticate(username=username, password=password)

        if user is not None:
            logging.info("user found" + str(user))
            if user.is_active:
                # this is where the user login actually happens, before this the user
                # is not logged in.
                auth.login(request, user)
                if request.POST.get('remember_me'):
                    request.session.set_expiry(1209600) # 2 weeks

                return redirect("index")
            else:
                messages.error(request, "Your account is not yet active" )
        else:
            messages.error(request, "Username or password incorrect" )

    return render(request = request,
                  template_name = "registration/login.html",
                  context={"form":form})

def register(request):
    if request.method == "POST":
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            user.is_active = False
            user.save()

            username = form.cleaned_data.get('username')
            logging.info("User created: " + str(user))
            welcome_text = "Hi {}, your registration was successful, an admin must activate your account before you can login"
            messages.info(request, welcome_text.format(username) )
       
            return redirect("login")

        else:
            logging.error("User not valid")
            messages.error(request, "Registration unsuccessful" )

    form = CustomSignUpForm
    return render(request = request,
                  template_name = "registration/sign_up.html",
                  context={"form":form})