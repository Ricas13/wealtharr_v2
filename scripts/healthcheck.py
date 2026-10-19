"""Docker health probe: actual Gunicorn listener and DB without HTTP redirects.

The public /health/ route is behind Django's HTTPS redirect; probing it over
localhost HTTP can falsely mark every production container unhealthy.
"""
import os
import socket

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wealtharr.settings")

import django  # noqa: E402
django.setup()
from django.db import connection  # noqa: E402

with socket.create_connection(("127.0.0.1", 8000), timeout=4):
    pass
with connection.cursor() as cursor:
    cursor.execute("SELECT 1")
    assert cursor.fetchone()[0] == 1
