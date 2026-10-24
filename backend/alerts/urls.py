from django.urls import path
from alerts import views

urlpatterns = [
    # Manual emergency voice dispatch & status
    path("trigger-call/", views.TriggerCallView.as_view(), name="trigger_call"),
    path("call-status/<str:call_sid>/", views.CallStatusView.as_view(), name="call_status"),
    path("history/", views.CallHistoryListView.as_view(), name="call_history"),
    path("preview-message/", views.PreviewMessageView.as_view(), name="preview_message"),
    # Recipient management
    path("recipients/", views.EmergencyRecipientListCreateView.as_view(), name="recipients_list_create"),
    # Twilio Webhook endpoints
    path("twilio/status-callback/", views.TwilioStatusCallbackView.as_view(), name="twilio_status_callback"),
    path("twilio/twiml/<int:record_id>/", views.TwilioTwiMLView.as_view(), name="twilio_twiml"),
    # ESP32 IoT Sensor Intake
    path("road-blockage/", views.ESP32RoadBlockageView.as_view(), name="esp32_road_blockage"),
    path("camera-event/", views.ESPCameraEventView.as_view(), name="esp_camera_event"),
]
