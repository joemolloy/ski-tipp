from django.shortcuts import render, get_object_or_404
from django import http
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import models
from django.db.models import Exists, OuterRef
from django.http import HttpResponseRedirect
from django.urls import reverse

from dal import autocomplete

from django.views.generic.edit import CreateView, UpdateView
from django.views.generic.detail import DetailView
from django.views.generic.list import ListView

from skitipp.forms import *
from skitipp.models import RaceEvent, Racer


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
    ordering = ['race_date']

    def get_queryset(self):

        #annotate if tipp was made for this race
        valid_tipp = Tipp.objects.filter(
            race_event=OuterRef('pk'),
            tipper=self.request.user,
            #created_at__gte=one_day_ago,
        )
        return RaceEvent.objects.all().annotate(user_has_tipped=Exists(valid_tipp))


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
        tipps = Tipp.objects.filter(tipper=self.request.user, race_event_id=self.kwargs['pk']).order_by('-created')[:1]
        if len(tipps):
            context['current_tipp'] = tipps[0]
            print(tipps[0].dnf)
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


from skitipp import fis_connector

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

def update_race(request, race_id):
    race = fis_connector.get_race_results(race_id)
    return HttpResponseRedirect(race.get_absolute_url())
