"""
Push sending. The default LoggingSender only logs (and records in memory for tests).
Set FCM_CREDENTIALS_FILE to a Firebase service-account JSON and install
firebase-admin to switch to real Firebase Cloud Messaging.
"""
import logging

from django.conf import settings
from django.utils.module_loading import import_string

log = logging.getLogger(__name__)


class LoggingSender:
    sent = []  # (token, title, body, data), inspected by tests

    def send(self, tokens, title, body, data):
        for t in tokens:
            self.sent.append((t, title, body, data))
            log.info("push -> %s: %s", t[:8], title)
        return []  # no invalid tokens


class FCMSender:
    _app = None

    def _init(self):
        import firebase_admin
        from firebase_admin import credentials

        if FCMSender._app is None:
            cred = credentials.Certificate(settings.PUSH["FCM_CREDENTIALS_FILE"])
            FCMSender._app = firebase_admin.initialize_app(cred)
        return FCMSender._app

    def send(self, tokens, title, body, data):
        from firebase_admin import messaging

        self._init()
        message = messaging.MulticastMessage(
            tokens=list(tokens),
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in (data or {}).items()},
        )
        response = messaging.send_each_for_multicast(message)
        invalid = []
        for tok, r in zip(tokens, response.responses):
            if not r.success and isinstance(r.exception, (messaging.UnregisteredError,)):
                invalid.append(tok)
        return invalid


def send_push(user, title, body, data=None):
    """Push to all of a user's devices. Never raises: a push failure must not break reporting."""
    from .models import DeviceToken

    tokens = list(user.device_tokens.values_list("token", flat=True))
    if not tokens:
        return 0
    try:
        invalid = import_string(settings.PUSH["SENDER"])().send(tokens, title, body, data or {})
        if invalid:
            DeviceToken.objects.filter(token__in=invalid).delete()
        return len(tokens) - len(invalid or [])
    except Exception:  # noqa: BLE001 - deliberately broad, see docstring
        log.exception("push send failed")
        return 0
