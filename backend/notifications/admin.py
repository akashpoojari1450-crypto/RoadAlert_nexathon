from django.contrib import admin

from .models import DeviceToken, Notification

admin.site.register(DeviceToken)
admin.site.register(Notification)
