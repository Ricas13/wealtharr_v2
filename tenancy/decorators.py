"""Guard a view with a feature."""
from functools import wraps

from django.http import HttpResponseForbidden

from . import access


def require_feature(key):
    """403 unless the signed-in user's plan includes ``key``. Use after login_required."""
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not access.can(request.user, key):
                return HttpResponseForbidden("Your plan does not include this feature.")
            return view(request, *args, **kwargs)
        return wrapped
    return decorator
