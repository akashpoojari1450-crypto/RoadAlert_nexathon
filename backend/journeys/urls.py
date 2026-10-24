from django.urls import path

from . import views

urlpatterns = [
    path("", views.MyJourneysView.as_view()),
    path("start/", views.StartJourneyView.as_view()),
    path("<int:pk>/points/", views.GpsBatchView.as_view()),
    path("<int:pk>/end/", views.EndJourneyView.as_view()),
]
