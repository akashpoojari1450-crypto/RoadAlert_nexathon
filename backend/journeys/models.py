from django.conf import settings
from django.db import models


class Journey(models.Model):
    """A trip the user starts. GPS points only exist while a journey is active."""

    class Kind(models.TextChoices):
        COMMUTE = "commute", "Commute"
        OFFICIAL_TRAVEL = "official_travel", "Official travel"
        PERSONAL = "personal", "Personal"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="journeys")
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.COMMUTE)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    origin_name = models.CharField(max_length=200, blank=True)
    destination_name = models.CharField(max_length=200, blank=True)
    origin_lat = models.FloatField()
    origin_lng = models.FloatField()
    dest_lat = models.FloatField()
    dest_lng = models.FloatField()
    # True only if employee consent + company policy allowed sharing at start time.
    shared_with_employer = models.BooleanField(default=False)
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    expected_minutes = models.PositiveIntegerField(null=True, blank=True)
    # The route the user chose, as GeoJSON-order [[lng, lat], ...]. Lets the server
    # warn people whose route passes a new incident.
    route_geometry = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.user} {self.origin_name}->{self.destination_name}"


class GpsPoint(models.Model):
    journey = models.ForeignKey(Journey, on_delete=models.CASCADE, related_name="points")
    latitude = models.FloatField()
    longitude = models.FloatField()
    speed_kmh = models.FloatField(default=0)
    recorded_at = models.DateTimeField()

    class Meta:
        ordering = ["recorded_at"]
        indexes = [models.Index(fields=["journey", "recorded_at"])]
