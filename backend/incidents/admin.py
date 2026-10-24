from django.contrib import admin

from .models import Incident, IncidentReport


class ReportInline(admin.TabularInline):
    model = IncidentReport
    extra = 0
    readonly_fields = ("reporter", "incident_type", "latitude", "longitude", "speed_kmh", "created_at")


@admin.register(Incident)
class IncidentAdmin(admin.ModelAdmin):
    list_display = ("incident_type", "road_name", "severity", "verification_level", "confidence",
                    "report_count", "started_at", "resolved_at")
    list_filter = ("incident_type", "severity", "verification_level")
    inlines = [ReportInline]
