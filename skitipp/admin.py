from django.contrib import admin

# Register your models here.

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from django import forms

class UserCreateForm(UserCreationForm):

    class Meta:
        model = User
        fields = ('username', 'email' , 'first_name', 'last_name', )

    def clean_email(self):
        cleaned_data = super().clean()
        email = cleaned_data.get("email")
        
        if not email or email == "":
            raise forms.ValidationError(
                    "Please provide an email address"
                )
        return email


from skitipp.models import Tipper

# Define an inline admin descriptor for Employee model
# which acts a bit like a singleton
class TipperInline(admin.StackedInline):
    model = Tipper
    can_delete = False
    verbose_name_plural = 'tipper'


class UserAdmin(UserAdmin):

    inlines = (TipperInline,)

    add_form = UserCreateForm
    #prepopulated_fields = {'username': ('first_name' , 'last_name', )}

    add_fieldsets = (
        (None, {
            'classes': ('wide', 'form-control'),
            'fields': ('username', 'email', 'password1', 'password2', ),
        }),
    )

# Re-register UserAdmin
admin.site.unregister(User)
admin.site.register(User, UserAdmin)
