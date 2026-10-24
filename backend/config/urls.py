from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from config.places import PlaceSearchView
from config.traffic import RouteTrafficView
from journeys.views import EmployerDashboardView

urlpatterns = [
    path("", include("dashboard.urls")),
    path("admin/", admin.site.urls),
    path("api/auth/", include("accounts.urls")),
    path("api/alerts/", include("alerts.urls")),
    path("api/incidents/", include("incidents.urls")),
    path("api/journeys/", include("journeys.urls")),
    path("api/routes/", include("routing.urls")),
    path("api/notifications/", include("notifications.urls")),
    path("api/employer/dashboard/", EmployerDashboardView.as_view()),
    path("api/places/search/", PlaceSearchView.as_view()),
    path("api/traffic/route/", RouteTrafficView.as_view()),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
