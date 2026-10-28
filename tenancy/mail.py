"""Plain system emails (verification, password reset) through the site's configured SMTP."""
import logging

from django.core.mail import EmailMessage, get_connection

LOG = logging.getLogger(__name__)


def send_system_email(to, subject, body):
    """True when handed to the mail server. Never raises: callers show a neutral message either way."""
    from tracker.models import SystemIntegration
    from tracker.services import decrypt_secret
    cfg = SystemIntegration.current()
    if not (cfg.smtp_host and cfg.smtp_sender):
        LOG.warning("System email not sent: SMTP is not configured")
        return False
    try:
        connection = get_connection(
            backend="django.core.mail.backends.smtp.EmailBackend",
            host=cfg.smtp_host, port=cfg.smtp_port, username=cfg.smtp_username or None,
            password=decrypt_secret(cfg.smtp_password_ciphertext) or None,
            use_tls=cfg.smtp_use_tls, timeout=10)
        EmailMessage(subject, body, cfg.smtp_sender, [to], connection=connection).send(fail_silently=False)
        return True
    except Exception:
        LOG.exception("System email failed")
        return False
