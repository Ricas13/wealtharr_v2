def chrome(request):
    if not request.user.is_authenticated:
        return {"nav_strategies": []}
    return {"nav_strategies": request.user.strategies.select_related("account").all()}
