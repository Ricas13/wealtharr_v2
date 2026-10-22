"""Conservative browser policies; compatible with Cloudflare Turnstile."""
class RestrictiveResponseHeaders:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        response["Cross-Origin-Resource-Policy"] = "same-origin"
        return response
