from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import TrackingPolicy, User
from .permissions import IsEmployer


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ["username", "password", "email", "phone"]

    def create(self, validated):
        # Public sign-up is always a passenger. Employees are added by their employer;
        # authority/admin accounts are created by an administrator.
        return User.objects.create_user(role=User.Role.PASSENGER, **validated)


class UserSerializer(serializers.ModelSerializer):
    company = serializers.StringRelatedField()

    class Meta:
        model = User
        fields = ["id", "username", "email", "phone", "role", "company", "tracking_consent", "consent_given_at"]
        read_only_fields = fields


class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        s = RegisterSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = s.save()
        token, _ = Token.objects.get_or_create(user=user)
        return Response({"token": token.key, "user": UserSerializer(user).data}, status=status.HTTP_201_CREATED)


class MeView(APIView):
    def get(self, request):
        return Response(UserSerializer(request.user).data)


class ConsentView(APIView):
    """
    The employee controls tracking consent. Revoking takes effect immediately:
    existing journeys stop being shared with the employer.
    """

    def get(self, request):
        return Response(_consent_state(request.user))

    def post(self, request):
        granted = bool(request.data.get("granted"))
        user = request.user
        if user.role != User.Role.EMPLOYEE:
            return Response({"detail": "Only employees have tracking consent."}, status=400)
        user.tracking_consent = granted
        user.consent_given_at = timezone.now() if granted else None
        user.save(update_fields=["tracking_consent", "consent_given_at"])
        if not granted:
            user.journeys.filter(shared_with_employer=True).update(shared_with_employer=False)
        return Response(_consent_state(user))


def _consent_state(user):
    return {
        "tracking_consent": user.tracking_consent,
        "consent_given_at": user.consent_given_at,
        "company": str(user.company) if user.company else None,
    }


class AddEmployeeView(APIView):
    """Employer registers an employee into their company. Consent still starts off."""

    permission_classes = [IsEmployer]

    def post(self, request):
        s = RegisterSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        employee = User.objects.create_user(
            role=User.Role.EMPLOYEE, company=request.user.company, **s.validated_data
        )
        return Response(UserSerializer(employee).data, status=status.HTTP_201_CREATED)


class PolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = TrackingPolicy
        fields = ["scope", "working_hours_start", "working_hours_end", "emergency_tracking_enabled"]


class PolicyView(APIView):
    """Employer reads/sets the company's tracking policy; employees can read it."""

    def get(self, request):
        company = request.user.company
        if not company:
            return Response({"detail": "No company."}, status=404)
        policy, _ = TrackingPolicy.objects.get_or_create(company=company)
        return Response(PolicySerializer(policy).data)

    def put(self, request):
        if not IsEmployer().has_permission(request, self):
            return Response({"detail": IsEmployer.message}, status=403)
        policy, _ = TrackingPolicy.objects.get_or_create(company=request.user.company)
        s = PolicySerializer(policy, data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        s.save()
        return Response(s.data)
