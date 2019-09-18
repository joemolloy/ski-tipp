from django.shortcuts import render, get_object_or_404
from django import http
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import models

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


