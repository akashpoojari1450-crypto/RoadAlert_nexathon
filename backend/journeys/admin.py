from django.contrib import admin

from .models import GpsPoint, Journey

admin.site.register(Journey)
admin.site.register(GpsPoint)
