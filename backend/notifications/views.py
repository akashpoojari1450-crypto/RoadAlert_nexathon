from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import DeviceToken, Notification


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id", "kind", "title", "body", "incident", "created_at", "read_at"]


class DeviceInput(serializers.Serializer):
    token = serializers.CharField(max_length=512)
    platform = serializers.ChoiceField(choices=["android", "ios", "web"], default="android")


class DeviceView(APIView):
    """Register this device for push. A token belongs to one user: re-registering moves it."""

    def post(self, request):
        s = DeviceInput(data=request.data)
        s.is_valid(raise_exception=True)
        DeviceToken.objects.update_or_create(
            token=s.validated_data["token"],
            defaults={"user": request.user, "platform": s.validated_data["platform"]},
        )
        return Response({"registered": True}, status=201)

    def delete(self, request):
        """Call on logout so the next person on a shared phone doesn't get your alerts."""
        DeviceToken.objects.filter(user=request.user, token=request.data.get("token", "")).delete()
        return Response(status=204)


class InboxView(APIView):
    def get(self, request):
        qs = request.user.notifications.all()
        if request.query_params.get("unread") == "1":
            qs = qs.filter(read_at__isnull=True)
        return Response(NotificationSerializer(qs[:100], many=True).data)


class MarkReadView(APIView):
    def post(self, request, pk):
        note = get_object_or_404(Notification, pk=pk, user=request.user)
        if note.read_at is None:
            note.read_at = timezone.now()
            note.save(update_fields=["read_at"])
        return Response(NotificationSerializer(note).data)
