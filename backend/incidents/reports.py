"""Monthly statistics for traffic authorities (API and web page share this)."""
from datetime import datetime

from django.db.models import Count, Sum
from django.utils import timezone

from .models import Incident, IncidentType, VerificationLevel


def month_bounds(year, month):
    """Aware [start, end) datetimes for a month. Raises ValueError on a bad year/month."""
    start = timezone.make_aware(datetime(year, month, 1))
    end = timezone.make_aware(datetime(year + (month == 12), (month % 12) + 1, 1))
    return start, end


def monthly_report(year, month):
    start, end = month_bounds(year, month)
    qs = Incident.objects.filter(started_at__gte=start, started_at__lt=end)
    by_reason = (
        qs.values("incident_type")
        .annotate(incidents=Count("id"), delay_min=Sum("estimated_delay_min"))
        .order_by("-incidents")
    )
    labels = dict(IncidentType.choices)
    reasons = [
        {
            "reason": labels.get(r["incident_type"], r["incident_type"]),
            "incidents": r["incidents"],
            "total_delay_hours": round((r["delay_min"] or 0) / 60, 1),
        }
        for r in by_reason
    ]
    hotspots = (
        qs.exclude(road_name="")
        .values("road_name")
        .annotate(incidents=Count("id"), delay_min=Sum("estimated_delay_min"))
        .order_by("-incidents", "-delay_min")[:10]
    )
    delays = [i.estimated_delay_min for i in qs if i.estimated_delay_min]
    return {
        "period": f"{year}-{month:02d}",
        "total_incidents": qs.count(),
        "total_delay_hours": round(sum(delays) / 60, 1),
        "average_delay_min": round(sum(delays) / len(delays), 1) if delays else 0,
        "max_delay_min": max(delays) if delays else 0,
        "most_frequent_reason": reasons[0]["reason"] if reasons else None,
        "by_reason": reasons,
        "hotspots": [
            {"road": h["road_name"], "incidents": h["incidents"],
             "total_delay_hours": round((h["delay_min"] or 0) / 60, 1)}
            for h in hotspots
        ],
        "authority_confirmed": qs.filter(verification_level=VerificationLevel.AUTHORITY_CONFIRMED).count(),
    }
