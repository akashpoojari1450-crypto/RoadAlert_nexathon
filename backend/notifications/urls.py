from django.urls import path

from . import views

urlpatterns = [
    path("", views.InboxView.as_view()),
    path("devices/", views.DeviceView.as_view()),
    path("<int:pk>/read/", views.MarkReadView.as_view()),
]
