from django.contrib.auth.models import AbstractUser
from django.db import models


class Company(models.Model):
    name = models.CharField(max_length=200, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "companies"

    def __str__(self):
        return self.name


class User(AbstractUser):
    """One user model for all five categories in the plan."""

    class Role(models.TextChoices):
        PASSENGER = "passenger", "Passenger"
        EMPLOYEE = "employee", "Employee"
        EMPLOYER = "employer", "Employer"
        AUTHORITY = "authority", "Traffic Authority"
        ADMIN = "admin", "System Administrator"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.PASSENGER)
    company = models.ForeignKey(
        Company, null=True, blank=True, on_delete=models.SET_NULL, related_name="members"
    )
    phone = models.CharField(max_length=20, blank=True)

    # Privacy: the employee decides whether the company may see journeys.
    # Tracking is only ever active for journeys the employee starts, and only
    # if the company policy allows it AND the employee has consented.
    tracking_consent = models.BooleanField(default=False)
    consent_given_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.username} ({self.role})"

    @property
    def is_employer_side(self):
        return self.role in (self.Role.EMPLOYER, self.Role.ADMIN)


class TrackingPolicy(models.Model):
    """Per-company policy limiting when employee tracking can happen."""

    class Scope(models.TextChoices):
        COMMUTE = "commute", "Employee commute only"
        OFFICIAL_TRAVEL = "official_travel", "Official travel only"
        JOURNEY_STARTED = "journey_started", "Only when employee starts a journey"

    company = models.OneToOneField(Company, on_delete=models.CASCADE, related_name="policy")
    scope = models.CharField(max_length=30, choices=Scope.choices, default=Scope.JOURNEY_STARTED)
    working_hours_start = models.TimeField(null=True, blank=True)
    working_hours_end = models.TimeField(null=True, blank=True)
    emergency_tracking_enabled = models.BooleanField(default=False)

    def __str__(self):
        return f"Policy for {self.company}"
