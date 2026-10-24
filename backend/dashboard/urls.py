from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("login/", auth_views.LoginView.as_view(template_name="dashboard/login.html", redirect_authenticated_user=True), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("employer/", views.EmployerPage.as_view(), name="employer"),
    path("authority/", views.AuthorityPage.as_view(), name="authority"),
    path("authority/incidents/<int:pk>/<str:action>/", views.incident_action, name="incident_action"),
]
