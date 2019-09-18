from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from dal import forward
from dal import autocomplete

from .models import RaceEvent, Tipp

class RaceEventForm(forms.ModelForm):
    class Meta:
        model = RaceEvent

        fields = ['location','kind','race_date',
            'fis_id','finished','points_multiplier']

class TippForm(forms.ModelForm):
    class Meta:
        model = Tipp

        fields = ['race_event','tipper', 'place_1','place_2','place_3','dnf','comment']
        widgets = {
            'race_event': forms.HiddenInput(),
            'tipper': forms.HiddenInput(),
            'place_1': autocomplete.ModelSelect2(url='racer-autocomplete'),
            'place_2': autocomplete.ModelSelect2(url='racer-autocomplete'),
            'place_3': autocomplete.ModelSelect2(url='racer-autocomplete'),
            'dnf': autocomplete.ModelSelect2(url='racer-autocomplete', forward=(forward.Const('dnf', 'racer_type'),)),
        }

    def __init__(self, *args, **kwargs):

        self.race_event = kwargs.pop('race_event', None)
        self.tipper = kwargs.pop('tipper', None)

        super(TippForm, self).__init__(*args, **kwargs)
        
    #validation
    def clean(self):

        cleaned_data = super().clean()

        race_event = cleaned_data.get("race_event")
        dnf = cleaned_data.get("dnf")

        print(race_event)
        if race_event.dnf_eligible and dnf is None:
            raise ValidationError(_('DNF or Alle im Ziel?'))

        

        