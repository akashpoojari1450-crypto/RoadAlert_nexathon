from django.urls import path

from . import views

urlpatterns = [
    path("report/", views.ReportView.as_view()),
    path("nearby/", views.NearbyView.as_view()),
    path("reports/monthly/", views.MonthlyReportView.as_view()),
    path("<int:pk>/", views.IncidentDetailView.as_view()),
    path("<int:pk>/confirm/", views.AuthorityConfirmView.as_view()),
    path("<int:pk>/resolve/", views.ResolveView.as_view()),
]
