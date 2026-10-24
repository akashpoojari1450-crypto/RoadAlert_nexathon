import calendar

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from django.views.decorators.http import require_POST

from accounts.models import TrackingPolicy, User
from alerts.models import EmergencyCallRecord, EmergencyRecipient
from incidents.models import Incident
from incidents.reports import monthly_report
from incidents.verification import refresh_incident
from journeys.views import employer_rows


def _is_employer(user):
    return user.role in (User.Role.EMPLOYER, User.Role.ADMIN) and user.company_id is not None


def _is_authority(user):
    return user.role in (User.Role.AUTHORITY, User.Role.ADMIN)


@login_required
def home(request):
    """Send each person to the page for their role."""
    user = request.user
    if _is_authority(user):
        return redirect("authority")
    if _is_employer(user):
        return redirect("employer")
    return render(request, "dashboard/no_access.html", status=200)


class EmployerPage(LoginRequiredMixin, UserPassesTestMixin, View):
    def test_func(self):
        return _is_employer(self.request.user)

    def get(self, request):
        try:
            hours = min(max(int(request.GET.get("hours", 12)), 1), 72)
        except ValueError:
            hours = 12
        company = request.user.company
        rows = employer_rows(company, hours)
        employees = User.objects.filter(company=company, role=User.Role.EMPLOYEE)
        sharing = employees.filter(tracking_consent=True).count()
        policy = TrackingPolicy.objects.filter(company=company).first()
        return render(request, "dashboard/employer.html", {
            "company": company,
            "rows": rows,
            "hours": hours,
            "delayed": sum(1 for r in rows if r["status"] != "Normal"),
            "employee_count": employees.count(),
            "sharing_count": sharing,
            "policy": policy,
        })


class AuthorityPage(LoginRequiredMixin, UserPassesTestMixin, View):
    def test_func(self):
        return _is_authority(self.request.user)

    def get(self, request):
        now = timezone.now()
        try:
            year = int(request.GET.get("year", now.year))
            month = int(request.GET.get("month", now.month))
            report = monthly_report(year, month)
        except ValueError:
            year, month = now.year, now.month
            report = monthly_report(year, month)
        incidents = Incident.objects.filter(resolved_at__isnull=True).order_by("-verification_level", "-started_at")[:50]
        max_reason = max((r["incidents"] for r in report["by_reason"]), default=1)
        max_hot = max((h["incidents"] for h in report["hotspots"]), default=1)
        for r in report["by_reason"]:
            r["pct"] = round(100 * r["incidents"] / max_reason)
        for h in report["hotspots"]:
            h["pct"] = round(100 * h["incidents"] / max_hot)
        call_records = EmergencyCallRecord.objects.select_related("incident", "recipient").all()[:20]
        recipients = EmergencyRecipient.objects.filter(is_active=True, is_verified=True, consent_received=True, opt_out=False)
        return render(request, "dashboard/authority.html", {
            "months": [(n, calendar.month_name[n]) for n in range(1, 13)],
            "incidents": incidents,
            "report": report,
            "year": year,
            "month": month,
            "active_count": Incident.objects.filter(resolved_at__isnull=True).count(),
            "call_records": call_records,
            "recipients": recipients,
        })


@login_required
@require_POST
def incident_action(request, pk, action):
    """Confirm (Level 6) or resolve an incident from the authority page."""
    if not _is_authority(request.user):
        raise PermissionDenied
    if action not in ("confirm", "resolve"):
        raise Http404
    incident = get_object_or_404(Incident, pk=pk)
    if action == "confirm":
        incident.authority_confirmed = True
        incident.save(update_fields=["authority_confirmed"])
        refresh_incident(incident)
    else:
        incident.resolved_at = timezone.now()
        incident.save(update_fields=["resolved_at"])
    return redirect("authority")
