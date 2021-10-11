from django.shortcuts import render, get_object_or_404, redirect
from django import http
from django.contrib.auth.mixins import LoginRequiredMixin

from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages

from django.views.generic.base import ContextMixin

from django.db import models
from django.db.models import BooleanField
from django.db.models import Exists, OuterRef, Sum, Count, Case, CharField, Value, When, Q, F, Subquery, Value
from django.db.models.functions import Coalesce

from django.db.utils import OperationalError

import datetime

from django.http import HttpResponseRedirect, HttpResponse
from django.urls import reverse

from dal import autocomplete

from django.views.generic.edit import CreateView, UpdateView, DeleteView
from django.views.generic.detail import DetailView
from django.views.generic.list import ListView
from django.views.generic.base import TemplateView
import skitipp

from skitipp.forms import *
from skitipp.models import RaceEvent, Racer, TippPointTally, PointAdjustment, Season

from django.contrib.auth.models import User
from django.forms.models import model_to_dict

from django.core.mail import send_mail


from skitipp import tipp_scorer

import logging
from operator import itemgetter

import inspect

def select_season(request, season_id):
    get_object_or_404(Season, pk=season_id)
    return redirect('race_list', season_id=season_id)

def select_current_season(request):
    request.session['selected_season_pk'] = Season.objects.filter(current=True).first().pk
    return redirect('race_list_current')

class SeasonContextMixin(ContextMixin):
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        season_id = self.kwargs.get('season_id')

        context['selected_season'] = Season.objects.all().annotate(
            is_selected=Case(When(pk=season_id, then=Value(True)),default=Value(False), output_field=BooleanField())
        ).order_by('-is_selected','-current').first()
        return context

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

class RaceListView(LoginRequiredMixin, SeasonContextMixin, ListView):
    template_name = 'race_list.html'
    model = RaceEvent

    def get_queryset(self):

        #messages.success(self.request, "Welcome to ski-tipp!")
        current_season = Season.objects.filter(current=True).first()
        if current_season is None:
            current_season = Season.objects.all().last()

        selected_season = Season.objects.filter(pk=self.kwargs.get('season_id', current_season.pk)).first()
        #annotate if tipp was made for this race
        valid_tipp = Tipp.objects.filter(
            race_event=OuterRef('pk'),
            tipper=self.request.user,
            #created_at__gte=one_day_ago,
        )
        return RaceEvent.objects.filter(
            season=selected_season
        ).annotate(
            user_has_tipped=Exists(valid_tipp),
            race_status=Case(
                When(Q(in_progress=True) | Q(race_date__date__gte = datetime.date.today()), then=Value('Upcoming & In Progress')),
                default=Value('Finished'),
                output_field=CharField(),
            )
        ).order_by('-race_status', 'race_date')


    def get_context_data(self, **kwargs):

        #selected_season = Season.objects.filter(pk=self.kwargs.get('season_id', 2)).first()
        print('racelist', self.kwargs)
        context = super(RaceListView, self).get_context_data(**kwargs)
        context['race_list'] = 'active'
        #context['selected_season'] = selected_season
        return context

class RaceEventCreateView(LoginRequiredMixin, CreateView):
    template_name = 'race_event_form.html'
    form_class = RaceEventForm

class RaceEventEditView(LoginRequiredMixin, SeasonContextMixin, UpdateView):
    template_name = 'race_event_form.html'
    model = RaceEvent
    form_class = RaceEventForm

class RaceEventDeleteView(LoginRequiredMixin, SeasonContextMixin, DeleteView):
    template_name = 'race_event_confirm_delete.html'
    model = RaceEvent
    success_url = '/app/racelist/'


class RaceEventDetailView(LoginRequiredMixin, SeasonContextMixin, DetailView):
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

class RaceResultsView(LoginRequiredMixin, SeasonContextMixin, DetailView):
    template_name = 'race_event_results.html'
    model = RaceEvent

class SeasonEditView(LoginRequiredMixin, SeasonContextMixin, UpdateView):
    template_name = 'season_edit_form.html'
    model = Season
    form_class = SeasonEditForm



class TippCreateView(LoginRequiredMixin, SeasonContextMixin, CreateView):
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
        race_event = self.object.race_event
        return race_event.get_absolute_url()

class ManualTippView(LoginRequiredMixin, SeasonContextMixin, CreateView):
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
def leaderboardDataView(request, season_id, race_kind):

    selected_season = get_object_or_404(Season,pk=season_id)
    if race_kind != 'Overall' and not selected_season.races.filter(kind=race_kind).exists():
        return HttpResponseNotFound('<h1>Page not found</h1>')

    best_tips = TippPointTally.objects.filter(race_event=OuterRef('pk')).filter(is_best_tipp=True)

    all_races = RaceEvent.objects.filter(
        season=selected_season
    ).annotate(
        alleine=Subquery(best_tips.values('tipper__username')[:1])
    ).order_by('race_date')

    if race_kind != 'Overall':
        all_races = all_races.filter(kind=race_kind)

    ranked_users = selected_season.tippers.annotate(
        preseason_adj=Coalesce(Sum('points_adjustments__points', 
            filter=Q(points_adjustments__preseason=True)&Q(points_adjustments__season=selected_season)), Value(0)),
        season_adj=Coalesce(Sum('points_adjustments__points', 
        filter=Q(points_adjustments__preseason=False)&Q(points_adjustments__season=selected_season)), Value(0)),
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

    totals = [u['total'] for u in leaderboard]
    ranks = [sorted(totals, reverse=True).index(x) + 1 for x in totals]

    for i, u in enumerate(leaderboard):
        u['rank'] = ranks[i]

    data = { "races" : race_list, "tippers" : leaderboard }

    return http.JsonResponse(data, json_dumps_params={'indent': 4} )


class LeaderboardView(LoginRequiredMixin, SeasonContextMixin, TemplateView):
    template_name = "leaderboard.html"

    def get_context_data(self, **kwargs):
        context = super(LeaderboardView, self).get_context_data(**kwargs)
        context['leaderboard'] = 'active'
        context['discipline'] = kwargs['race_kind']
        return context

from skitipp import fis_connector

@staff_member_required
def upload_race(request, season_id):

    selected_season = get_object_or_404(Season, pk=season_id)
    # if this is a POST request we need to process the form data
    if request.method == 'POST':
        # create a form instance and populate it with data from the request:
        form = UploadRaceForm(request.POST)
        # check whether it's valid:

        if form.is_valid():
            # process the data in form.cleaned_data as required
            race_fis_id = form.cleaned_data['fis_id']
            race_season = form.cleaned_data['season']

            race_event, created = fis_connector.get_new_race_results(race_fis_id, race_season)

            # redirect to a new URL:
            #return HttpResponseRedirect(reverse('race_detail', kwargs={'pk': race_fis_id}))
            if created:
                messages.info(request, 'Race created: ' + str(race_fis_id) + ' in season ' + str(race_season) + ' - ' + race_event.short_name)
            else:
                messages.warning(request, 'Race alreaday exists: ' + str(race_fis_id) + ' in season ' + str(race_season) + ' - ' + race_event.short_name)

            return HttpResponseRedirect(reverse('upload_race', kwargs={'season_id': selected_season.pk}))
        else:
            messages.error(request, 'input invalid')

    # if a GET (or any other method) we'll create a blank form
    else:
        form = UploadRaceForm(initial = {'season':selected_season})
        return render(request, 'upload_race_form.html', {'form': form, 'selected_season': selected_season})


from skitipp import race_web_scraper

@staff_member_required
def upload_races_bulk(request, season_id):

    selected_season = get_object_or_404(Season, pk=season_id)
    # if this is a POST request we need to process the form data

    print(request.POST)

    if request.is_ajax and request.method == 'GET':
        
        #download calendar and format for page
        calendar_link = get_object_or_404(Season, pk=season_id).fis_calendar
        events = race_web_scraper.get_season_events(calendar_link, season_id)

        if events:
            return render(request, 'upload_race_bulk_form.html', 
                {'selected_season': selected_season, 'events': events})
        else:
            response = http.JsonResponse({"error": "Please add a valid calendar link to the season"})
            response.status_code = 400 
            return response

    elif request.is_ajax and request.method == 'POST':
        print('loading races')
        
        created_races = []
        existing_races = []
        
        #add all the requested races if they don't exist already
        for k,v in request.POST.items():
            print(k,v)
            if k.startswith('race-checkbox-') and v == 'on':
                race_fis_id = int(k.replace('race-checkbox-',''))
                race_event, created = fis_connector.get_new_race_results(race_fis_id, selected_season)
                if created:
                    created_races.append(race_event.short_name)
                else:
                    existing_races.append(race_event.short_name)

        if created_races:
            messages.success(request, 'The races ' + ', '.join(created_races) + ' were created')
        if existing_races:
            messages.warning(request, 'The races ' + ', '.join(existing_races) + ' already existed')

        return redirect('edit_season', selected_season.pk)
    # if a GET (or any other method) we'll create a blank form
    else:
        form = UploadRaceBulkForm(initial = {'season':selected_season})
        return render(request, 'upload_race_bulk_form.html', {'race_form': form, 'selected_season': selected_season})




@staff_member_required
def update_race(request, race_id):
    race_event = fis_connector.get_race_results(race_id)
    return HttpResponseRedirect(race_event.get_absolute_url())


def publish_tipps(request, race_id):
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
    return HttpResponseRedirect(reverse('race_list_current'))

@staff_member_required
def rescore_all_races(request):

    selected_season = get_selected_season(request)

    TippPointTally.objects.filter(race_event__season=selected_season).delete()

    for race_event in RaceEvent.objects.filter(season=selected_season, finished=True).order_by('race_date'):
        
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

class PointAdjustmentListView(SeasonContextMixin, CreateView):

    
    form_class = PointAdjustmentForm
    template_name = "point_adjustments.html"
    try:
        current_season = Season.objects.filter(current=True).first()
        initial={'season': current_season}
    except OperationalError as err:
        logging.error(err) 

    def get_context_data(self, **kwargs):
        context = super(PointAdjustmentListView, self).get_context_data(**kwargs)
        context['point_adjustment_list'] = context['selected_season'].points_adjustments.annotate(
            sign=Case(
                When(points__gt=0, then=Value('positive')),
                default=Value('negative'),
                output_field=CharField(),
            )
        ).order_by('-created')
        
        context['point_adjustments'] = 'active'

        return context

@staff_member_required
def deletePointAdjustment(request, season_id, adjustment_id):
    get_object_or_404(PointAdjustment, pk=adjustment_id).delete()
    return redirect('point_adjustments', season_id=season_id)

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
        form = CustomSignUpForm(request.POST)

        if form.is_valid():
            user = form.save()
            user.is_active = False
            user.email = form.cleaned_data.get('email')

            user.save()

            email_admin_about_registration(request, user)

            username = form.cleaned_data.get('username')
            logging.info("User created: " + str(user))
            welcome_text = "Hi {}, your registration was successful, an admin must activate your account before you can login"
            messages.info(request, welcome_text.format(username) )
       
            return redirect("login")
    else:
        form = CustomSignUpForm

    return render(request = request,
                  template_name = "registration/sign_up.html",
                  context={"form":form})

def email_admin_about_registration(request, user):
        from django.core.mail import send_mail
        from django.urls import reverse

        approval_link = request.build_absolute_uri(reverse('activate_user', kwargs=dict(username=user.username)))

        approver_email = User.objects.filter(username='Donkunho').get().email
        if approver_email is None or approver_email == '':
            approver_email = User.objects.filter(username='joemolloy').get().email

        send_mail(
            subject='There is a new registration on Ski-tipp.org',
            message=f'''{user.username} has registered on ski-tipp.org.

            Their email address is {user.email} 

            To approve the account, use the following link: {approval_link}''',
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list = [approver_email],
            fail_silently=False,
        )

@staff_member_required
def activate_user(request, username):
    user = get_object_or_404(User, username=username)
    user.is_active = True
    user.save()

    login_url = request.build_absolute_uri(reverse('login'))

    send_mail(
            subject='Ihr Konto auf Ski-tipp.org wurde aktiviert',
            message=inspect.cleandoc(
                f'''Hallo {user.username}
            
                Ihr Konto auf ski-tipp.org ist jetzt aktiv.

                Ihr Username zum einloggen is {user.username}.
                Ski können hier einloggen: {login_url}

                mit freundlichen Grüssen
                Ski-tipp.org

                '''
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list = [user.email],
            fail_silently=False,
        )

    return HttpResponse(f'The account for {username} has been activated')

