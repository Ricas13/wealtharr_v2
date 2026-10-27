"""Make the signed-in user the active tenant for the request."""
from .context import tenant_context


class TenantMiddleware:
    """Sets ``request.tenant_id`` and the tenant context (the user's id, or none if anonymous)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        tenant_id = user.pk if user is not None and user.is_authenticated else None
        request.tenant_id = tenant_id
        with tenant_context(tenant_id):
            return self.get_response(request)
