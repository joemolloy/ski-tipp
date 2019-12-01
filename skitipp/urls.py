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
    path('race/<int:race_id>/publish/', views.publish_tipps, name='publish_tipps'),
    path('race/<int:race_id>/finalize/', views.finalize_race, name='finalize_race'),
    path('leaderboard/rescore_races/', views.rescore_all_races, name='rescore_all_races'),

    path('leaderboard/', views.LeaderboardView.as_view(), name='leaderboard'),
    path('data/leaderboard/', views.leaderboardDataView, name='leaderboard_data'),

    path('point_adjustments/', views.PointAdjustmentListView.as_view(), name='point_adjustments'),
    path('point_adjustments/<int:adjustment_id>/delete/', views.deletePointAdjustment, name='delete_point_adjustment'),

    path('update_racers/', views.update_wc_start_list, name='update_racers'),

    path(
        r'racer-autocomplete/',
        views.RacerAutocomplete.as_view(),
        name='racer-autocomplete',
    ),
]