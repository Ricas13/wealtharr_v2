"""A ceiling on state-changing requests per signed-in user, so one account (or a stolen session)
cannot hammer the ledger, exports or alert endpoints. Shared across workers through the database."""
from django.conf import settings
from django.http import HttpResponse

WINDOW_SECONDS = 60
SAFE = {"GET", "HEAD", "OPTIONS"}


class WriteRateLimit:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        limit = getattr(settings, "WRITE_RATE_LIMIT_PER_MINUTE", 0)
        user = getattr(request, "user", None)
        if limit and request.method not in SAFE and user is not None and user.is_authenticated:
            from tracker.rate_limits import increment
            if increment(f"writes:{user.pk}", WINDOW_SECONDS) > limit:
                response = HttpResponse("Too many requests. Wait a minute and try again.",
                                        status=429, content_type="text/plain")
                response["Retry-After"] = str(WINDOW_SECONDS)
                return response
        return self.get_response(request)
