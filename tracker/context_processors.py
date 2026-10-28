from .tenant_scope import strategies_of


def chrome(request):
    if not request.user.is_authenticated:
        return {"nav_strategies": []}
    return {"nav_strategies": strategies_of(request.user).select_related("account")}


def public_site(request):
    from .seo import site_url
    return {"site_url": site_url(request)}
