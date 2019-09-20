from django.shortcuts import render, get_object_or_404, redirect
from django import http
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages

from django.db import models
from django.db.models import Exists, OuterRef, Sum, Count, Case, CharField, Value, When, Q, F, Subquery


from django.http import HttpResponseRedirect
from django.urls import reverse

from dal import autocomplete

from django.views.generic.edit import CreateView, UpdateView
from django.views.generic.detail import DetailView
from django.views.generic.list import ListView
from django.views.generic.base import TemplateView

from skitipp.forms import *
from skitipp.models import RaceEvent, Racer, TippPointTally, PointAdjustment

from django.contrib.auth.models import User
from django.forms.models import model_to_dict

from skitipp import tipp_scorer

import logging

class RacerAutocomplete(LoginRequiredMixin, autocomplete.Select2QuerySetView):
    def get_queryset(self):
        field = self.forwarded.get('racer_type', None)
        if field == 'dnf':
            qs = Racer.objects.all()
        else:
            qs = Racer.objects.competitors()

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
                When(finished=True, then=Value('Finished')),
                default=Value('Upcoming'),
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
    
    success_url = '/app/racelist/'

    def get_context_data(self, **kwargs):
        context = super(TippCreateView, self).get_context_data(**kwargs)
        context['race_event'] = get_object_or_404(RaceEvent, pk=self.kwargs['race_id'])

        return context

    def get_form_kwargs(self):
        kwargs = super(TippCreateView, self).get_form_kwargs()
        kwargs['initial']['race_event'] = RaceEvent.objects.get(pk=self.kwargs['race_id'])
        kwargs['initial']['tipper'] = self.request.user

        return kwargs


from collections import defaultdict, OrderedDict

@login_required
def leaderboardDataView(request):

    best_tips = TippPointTally.objects.filter(race_event=OuterRef('pk')).filter(is_best_tipp=True)

    all_races = RaceEvent.objects.all().annotate(
        alleine=Subquery(best_tips.values('tipper__username')[:1])
    ).order_by('race_date')

    ranked_users = User.objects.all().annotate(race_total=Sum('user_points_tally__total_points'))


    adjustments = User.objects.all().annotate(
        all_adj=Sum('points_adjustments__points'),
        preseason_adj=Sum('points_adjustments__points', filter=Q(points_adjustments__preseason=True)),
        season_adj=Sum('points_adjustments__points', filter=Q(points_adjustments__preseason=False))
    ).order_by('-total_points')
    

    #tally up the points for the race for each active user

    race_list =  list(all_races.values())
    leaderboard = []

    for u in ranked_users:
        user_adjustments = adjustments.get(id=u.id)

        race_total = u.race_total if u.race_total is not None else 0
        adj_total = user_adjustments.all_adj if user_adjustments.all_adj is not None else 0

        user_row = { 
            'user' : model_to_dict(u, fields=['id', 'username']), 
            'races' : [], 
            'race_total': race_total,
            'all_adj': adj_total ,
            'preseason_adj' : user_adjustments.preseason_adj, 
            'season_adj' : user_adjustments.season_adj,
            'total' : race_total + adj_total, 
        }

        user_race_points = u.user_points_tally

        for race in all_races:
            total_points = None
            did_tipp = None
            race_points = user_race_points.filter(race_event=race).first()
            if race_points:
                total_points = race_points.total_points
                did_tipp = race_points.did_tipp
                #total_points = int(total_points) if total_points.is_integer() else total_points
            else:
                total_points = None
            user_row['races'].append({'points': total_points, 'did_tipp': did_tipp })

        leaderboard.append(user_row)

    data = { "races" : race_list, "data" : leaderboard }

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
    race_event.finished = True
    race_event.save(update_fields=['finished'])

    print("finializing race {}".format(race_event))
    #delete points from this race
    race_event.tipp_points_tally.delete()
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