"""Resolve the active organization for each request, validated against membership."""
from .context import tenant_context
from .services import default_membership, membership_for

SESSION_KEY = "wealtharr_active_org"


def switch_organization(request, organization_id):
    """Make another organization active, only if the user is a member. Returns the membership."""
    membership = membership_for(request.user, organization_id)
    if membership is not None:
        request.session[SESSION_KEY] = membership.organization_id
    return membership


class TenantMiddleware:
    """Sets ``request.organization`` and ``request.membership`` and the tenant context.

    The session may name an organization, but it is only honoured when the signed-in user
    is a member of it; a forged or stale value falls back to the user's default.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        membership = None
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            membership = (membership_for(user, request.session.get(SESSION_KEY))
                          or default_membership(user))
        request.membership = membership
        request.organization = membership.organization if membership else None
        with tenant_context(request.organization):
            return self.get_response(request)
