"""The visitor's address, as far as the proxy chain can be trusted.

Behind Cloudflare and Traefik the socket address is the proxy's, and the client
controls everything it puts in X-Forwarded-For *before* the trusted proxies add
their own entries. ``TRUSTED_PROXY_HOPS`` is how many proxies the deployment
puts in front of the app (each appends the address it saw); the client address
is that many entries from the end. With 0 (the default) nothing is trusted and
every visitor shares the socket address, so per-visitor limits are skipped.
"""
from django.conf import settings


def client_ip(request):
    """The trusted client address, or "" when it can't be told apart."""
    hops = settings.TRUSTED_PROXY_HOPS
    if hops <= 0:
        return ""
    parts = [p.strip() for p in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if p.strip()]
    return parts[-hops] if len(parts) >= hops else ""
