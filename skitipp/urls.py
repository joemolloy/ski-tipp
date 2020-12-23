from django.urls import path

from . import views

urlpatterns = [
    
    path('season/current/racelist/', views.RaceListView.as_view(), name='race_list_current'),

    path('season/<int:season_id>/racelist/', views.RaceListView.as_view(), name='race_list'),

    path('race/<int:pk>/detail/', views.RaceEventDetailView.as_view(), name='race_detail'),
    path('race/<int:pk>/results/', views.RaceResultsView.as_view(), name='race_results'),
    path('race/<int:pk>/edit/', views.RaceEventEditView.as_view(), name='edit_race'),
    path('race/<int:pk>/delete/', views.RaceEventDeleteView.as_view(), name='delete_race'),

    path('race/<int:race_id>/tipp/', views.TippCreateView.as_view(), name='create_tipp'),
    
    path('race/<int:race_id>/update/', views.update_race, name='update_race'),
    path('race/<int:race_id>/publish/', views.publish_tipps, name='publish_tipps'),
    path('race/<int:race_id>/finalize/', views.finalize_race, name='finalize_race'),
      
    path('season/<int:season_id>/race/create/', views.upload_race, name='upload_race'),

    path('season/<int:season_id>/race/<int:race_id>/manual_tipp/<tipper>', views.ManualTippView.as_view(), name='manual_tipp'),


    path('season/<int:season_id>/leaderboard/rescore_races/', views.rescore_all_races, name='rescore_all_races'),

    path('season/<int:season_id>/leaderboard/<str:race_kind>/', views.LeaderboardView.as_view(), name='leaderboard'),
    path('season/<int:season_id>/leaderboard/data/<str:race_kind>/', views.leaderboardDataView, name='leaderboard_data'),

    path('season/<int:season_id>/point_adjustments/', views.PointAdjustmentListView.as_view(), name='point_adjustments'),
    path('season/<int:season_id>/point_adjustments/<int:adjustment_id>/delete/', views.deletePointAdjustment, name='delete_point_adjustment'),

    path('season/select/<int:season_id>/', views.select_season, name='select_season'),
    path('season/select/current/', views.select_current_season, name='select_current_season'),

    path('season/<int:season_id>/update_racers/', views.update_wc_start_list, name='update_racers'),

    path(
        r'racer-autocomplete/',
        views.RacerAutocomplete.as_view(),
        name='racer-autocomplete',
    ),
]