"""istv URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/2.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.http import HttpResponseRedirect

from django.contrib.auth import views as auth_views

from skitipp.views import RaceListView, AboutView, register, login_view

from skitipp.forms import CustomLoginForm


admin.site.site_header = "Ski-tipp Admin"
admin.site.title = "Ski-tipp Admin"
admin.site.site = "Ski-tipp Admin"
admin.site.site_title = admin.site.site_header = "Ski-tipp Admin Portal"

admin.site.index_title = "Welcome to Ski-tipp.org"



urlpatterns = [
    path('admin/', admin.site.urls),
    path('user/login/', login_view, name='login'),
    path("user/signup", register, name="signup"),

    path('user/', include('django.contrib.auth.urls')), # new
    path('app/', include('skitipp.urls')),
    path('', RaceListView.as_view(), name='index'),
    path('about/', AboutView.as_view(), name='about'),
]
