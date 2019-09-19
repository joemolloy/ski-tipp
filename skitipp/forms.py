from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from dal import forward
from dal import autocomplete

from .models import RaceEvent, Tipp, PointAdjustment

class BootstrapFormMixin(object):

    def __init__(self, *args, **kwargs):
        super(BootstrapFormMixin, self).__init__(*args, **kwargs)
        # add common css classes to all widgets
        for field in iter(self.fields):
            #get current classes from Meta
            classes = self.fields[field].widget.attrs.get("class")
            
            if classes is None:
                classes = ""
            
            if self.fields[field].widget.input_type == 'checkbox':
                classes += " form-check form-check-inline"
            else:
                classes = "form-control"

            self.fields[field].widget.attrs.update({
                'class': classes
            })
        
class RaceEventForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = RaceEvent

        fields = ['finished','points_multiplier', 'short_name']
        
class TippForm(BootstrapFormMixin, forms.ModelForm):
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

        
class UploadRaceForm(BootstrapFormMixin, forms.Form):
    fis_id = forms.IntegerField(label='FIS ID')

class PointAdjustmentForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = PointAdjustment
        fields = ['tipper', 'reason', 'points', 'preseason']
