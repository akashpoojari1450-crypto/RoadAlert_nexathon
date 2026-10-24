from django.urls import path
from rest_framework.authtoken.views import obtain_auth_token

from . import views

urlpatterns = [
    path("register/", views.RegisterView.as_view()),
    path("login/", obtain_auth_token),
    path("me/", views.MeView.as_view()),
    path("consent/", views.ConsentView.as_view()),
    path("policy/", views.PolicyView.as_view()),
    path("employees/", views.AddEmployeeView.as_view()),
]
