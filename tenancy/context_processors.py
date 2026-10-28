def signup(request):
    """Lets public pages show "Create account" only when sign-up is open (flag on and mail set up)."""
    from .accounts import signup_enabled
    return {"signup_open": signup_enabled()}
