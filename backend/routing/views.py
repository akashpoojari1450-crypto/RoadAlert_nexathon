from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from incidents.models import Incident

from . import services
from .providers import RoutingError


class PointSerializer(serializers.Serializer):
    lat = serializers.FloatField(min_value=-90, max_value=90)
    lng = serializers.FloatField(min_value=-180, max_value=180)


class PlanInput(serializers.Serializer):
    origin = PointSerializer()
    destination = PointSerializer()


class AvoidInput(PlanInput):
    incident_id = serializers.IntegerField()


def _pt(p):
    return (p["lat"], p["lng"])


def _strip(route):
    return {k: v for k, v in route.items() if not k.startswith("_")}


class PlanView(APIView):
    """POST /api/routes/plan/ - candidate routes, incidents on each, and a recommendation."""

    def post(self, request):
        s = PlanInput(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        try:
            result = services.plan(_pt(d["origin"]), _pt(d["destination"]))
        except RoutingError as exc:
            return Response({"detail": str(exc)}, status=503)
        result["routes"] = [_strip(r) for r in result["routes"]]
        return Response(result)


class AvoidView(APIView):
    """POST /api/routes/avoid/ - the 'Avoid This Route' button."""

    def post(self, request):
        s = AvoidInput(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        incident = get_object_or_404(Incident, pk=d["incident_id"])
        try:
            result = services.avoid(_pt(d["origin"]), _pt(d["destination"]), incident)
        except RoutingError as exc:
            return Response({"detail": str(exc)}, status=503)
        result["route"] = _strip(result["route"])
        return Response(result)
