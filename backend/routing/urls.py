from django.urls import path

from . import views

urlpatterns = [
    path("plan/", views.PlanView.as_view()),
    path("avoid/", views.AvoidView.as_view()),
]
