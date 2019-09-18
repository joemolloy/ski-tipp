from django.urls import path

from . import views

urlpatterns = [
    path('racelist/', views.RaceListView.as_view(), name='race_list'),

    path('race/<int:pk>/detail/', views.RaceEventDetailView.as_view(), name='race_detail'),
    path('race/<int:pk>/results/', views.RaceResultsView.as_view(), name='race_results'),
    path('race/<int:pk>/edit/', views.RaceEventEditView.as_view(), name='edit_race'),

    path('race/<int:race_id>/tipp/', views.TippCreateView.as_view(), name='create_tipp'),
    
    path('race/create/', views.upload_race, name='upload_race'),
    path('race/<int:race_id>/update/', views.update_race, name='update_race'),

    path(
        r'racer-autocomplete/',
        views.RacerAutocomplete.as_view(),
        name='racer-autocomplete',
    ),
]