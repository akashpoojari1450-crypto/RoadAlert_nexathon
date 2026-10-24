from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Company, TrackingPolicy, User


@admin.register(User)
class RoadAlertUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ("RoadAlert", {"fields": ("role", "company", "phone", "tracking_consent", "consent_given_at")}),
    )
    list_display = ("username", "role", "company", "tracking_consent", "is_active")
    list_filter = ("role", "company")


admin.site.register(Company)
admin.site.register(TrackingPolicy)
