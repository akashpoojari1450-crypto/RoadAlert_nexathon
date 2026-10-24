from django.conf import settings
from django.db import models


class DeviceToken(models.Model):
    """An FCM registration token for one of the user's devices."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="device_tokens")
    token = models.CharField(max_length=512, unique=True)
    platform = models.CharField(max_length=10, default="android")
    created_at = models.DateTimeField(auto_now_add=True)


class Notification(models.Model):
    class Kind(models.TextChoices):
        TRAFFIC_ALERT = "traffic_alert", "Traffic alert"
        EMPLOYER_ALERT = "employer_alert", "Employee travel alert"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    title = models.CharField(max_length=200)
    body = models.TextField()
    incident = models.ForeignKey("incidents.Incident", null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # One alert per person per incident per kind, so repeat reports don't spam anyone.
            models.UniqueConstraint(fields=["user", "incident", "kind"], name="one_alert_per_incident"),
        ]
