"""The active tenant for the current request or job. Fail closed: no context, no data."""
from contextlib import contextmanager
from contextvars import ContextVar

_organization_id = ContextVar("wealtharr_organization_id", default=None)


class NoTenantContext(RuntimeError):
    """Tenant-owned data was asked for without saying whose."""


def current_organization_id():
    return _organization_id.get()


def require_organization_id():
    value = _organization_id.get()
    if value is None:
        raise NoTenantContext(
            "No active organization. Wrap the work in tenancy.context.tenant_context(...) "
            "or use all_tenants from platform code.")
    return value


@contextmanager
def tenant_context(organization):
    """Run a block as one organization (an Organization, an id, or None for none)."""
    value = getattr(organization, "pk", organization)
    token = _organization_id.set(value)
    try:
        yield value
    finally:
        _organization_id.reset(token)
