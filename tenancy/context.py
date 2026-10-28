"""The signed-in user whose data this request or job may touch. Fail closed: none, no data.

The tenant is the user: every user's records are theirs alone. There is no shared
organization, so the context is just the user's id.
"""
from contextlib import contextmanager
from contextvars import ContextVar

_tenant_id = ContextVar("wealtharr_tenant_id", default=None)


class NoTenantContext(RuntimeError):
    """Tenant-owned data was asked for without saying whose."""


def current_tenant_id():
    return _tenant_id.get()


def require_tenant_id():
    value = _tenant_id.get()
    if value is None:
        raise NoTenantContext(
            "No active user. Wrap the work in tenancy.context.tenant_context(user) "
            "or use the plain manager from platform code.")
    return value


@contextmanager
def tenant_context(tenant, *, bypass=False):
    """Run a block as one user (a user, an id, or None for nobody).

    ``bypass`` is for platform admins: the user is still the active user in Python, but with
    database row-level security on the connection may see every user's rows.
    """
    from . import rls
    value = getattr(tenant, "pk", tenant)
    token = _tenant_id.set(value)
    previous = rls.push(value, bypass=bypass)
    try:
        yield value
    finally:
        _tenant_id.reset(token)
        rls.pop(previous)


@contextmanager
def platform_context():
    """Trusted platform code (admin, jobs listing everyone's work). Not tied to one user.

    With database row-level security on, this is the only way to see rows across users.
    """
    from . import rls
    token = _tenant_id.set(None)
    previous = rls.push(None, bypass=True)
    try:
        yield
    finally:
        _tenant_id.reset(token)
        rls.pop(previous)


def per_tenant(items, user_id=lambda item: item.owner_id):
    """Yield each item inside its owner's tenant context (for jobs that loop over everyone).

    The context is set before each item is handed to the loop body and cleared afterwards, even
    if the body continues, breaks or raises.
    """
    with platform_context():
        items = list(items)  # evaluated across users, before any one user's context
    for item in items:
        with tenant_context(user_id(item)):
            yield item
