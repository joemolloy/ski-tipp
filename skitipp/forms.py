from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from dal import forward
from dal import autocomplete

from .models import RaceEvent, Tipp, PointAdjustment
import logging

class BootstrapFormMixin(object):

    def __init__(self, *args, **kwargs):
        super(BootstrapFormMixin, self).__init__(*args, **kwargs)
        # add common css classes to all widgets
        for field in iter(self.fields):
            #get current classes from Meta
            classes = self.fields[field].widget.attrs.get("class")
            
            if classes is None:
                classes = ""
            
            widget = self.fields[field].widget
            if hasattr(widget, 'input_type') and widget.input_type == 'checkbox':
                classes += " form-check form-check-inline"
            else:
                classes = "form-control"

            widget.attrs.update({
                'class': classes
            })
        
class RaceEventForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = RaceEvent

        fields = ['in_progress', 'finished','cancelled', 'points_multiplier', 'short_name']
        
class TippForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Tipp

        fields = ['race_event','tipper', 'place_1','place_2','place_3', 'dnf', 'comment']

        labels = {
                    'place_1': '1.',
                    'place_2': '2.',
                    'place_3': '3.',
                    'comment': 'Note:'
                }

        widgets = {
            'race_event': forms.HiddenInput(),
            'tipper': forms.HiddenInput(),
            'place_1': autocomplete.ModelSelect2(url='racer-autocomplete'),
            'place_2': autocomplete.ModelSelect2(url='racer-autocomplete'),
            'place_3': autocomplete.ModelSelect2(url='racer-autocomplete'),
            'dnf': autocomplete.ModelSelect2(url='racer-autocomplete', forward=(forward.Const('dnf', 'racer_type'),)),
            'comment': forms.Textarea(attrs={'rows':4, 'cols':15}),

        }


    def __init__(self, *args, **kwargs):
        super(TippForm, self).__init__(*args, **kwargs)

        self.race_event = kwargs.pop('race_event', None)
        self.tipper = kwargs.pop('tipper', None)
        print(kwargs)
        if not kwargs['initial']['race_event'].dnf_eligible:
            del self.fields['dnf']

        
    #validation
    def clean_dnf(self):
        cleaned_data = super().clean()
        dnf = cleaned_data.get("dnf")

        race_event = cleaned_data.get("race_event")
                
        if race_event.dnf_eligible and dnf is None:
            raise forms.ValidationError(
                    "Please select Alle im Ziel or a DNF Racer"
                )
        return dnf

        
class UploadRaceForm(BootstrapFormMixin, forms.Form):
    fis_id = forms.IntegerField(label='FIS ID')

class PointAdjustmentForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = PointAdjustment
        fields = ['tipper', 'reason', 'points', 'preseason']

from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User
class CustomLoginForm(BootstrapFormMixin, AuthenticationForm):
    
    remember_me = forms.BooleanField(label="Remember Me?", required=False)



class CustomSignUpForm(BootstrapFormMixin, UserCreationForm):

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2")

    def save(self, commit=True):
        user = super(CustomSignUpForm, self).save(commit=False)
        user.is_active = False
        if commit:
            user.save()
        return user