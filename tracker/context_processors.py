def chrome(request):
    if not request.user.is_authenticated:
        return {"nav_strategies": []}
    return {"nav_strategies": request.user.strategies.select_related("account").all()}


def public_site(request):
    from .seo import site_url
    return {"site_url": site_url(request)}
