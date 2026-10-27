"""Creating and resolving organizations. Platform code, not request-scoped data access."""
from django.db import IntegrityError, transaction

from .models import Membership, Organization


def personal_org_for(user):
    """The user's own organization, created with an owner membership on first use."""
    existing = Organization.objects.filter(personal_owner=user).first()
    if existing is not None:
        return existing
    label = (getattr(user, "get_username", lambda: "")() or "Account")[:100]
    try:
        with transaction.atomic():
            organization = Organization.objects.create(name=label, personal_owner=user)
    except IntegrityError:  # a concurrent request created it first
        return Organization.objects.get(personal_owner=user)
    Membership.objects.get_or_create(
        user=user, organization=organization, defaults={"role": Membership.OWNER})
    return organization


def membership_for(user, organization_id):
    """The user's membership in an active organization, or None. Never trusts the caller."""
    if not getattr(user, "is_authenticated", False) or not organization_id:
        return None
    return (Membership.objects.select_related("organization")
            .filter(user=user, organization_id=organization_id,
                    organization__status=Organization.ACTIVE).first())


def default_membership(user):
    """Personal organization first, otherwise the oldest membership."""
    rows = Membership.objects.select_related("organization").filter(
        user=user, organization__status=Organization.ACTIVE)
    return (rows.filter(organization__personal_owner=user).first()
            or rows.order_by("created_at", "pk").first())
