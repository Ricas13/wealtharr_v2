"""The one place the app talks to a payment gateway over HTTPS. No redirects are followed, every
call has a timeout, and tests replace ``call`` so nothing real is contacted."""
import json
import urllib.error
import urllib.parse
import urllib.request


class GatewayError(Exception):
    """A gateway refused, timed out or answered nonsense. The message is safe to show the admin."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def call(method, url, *, headers=None, form=None, json_body=None, timeout=10):
    """(status, parsed JSON or {}). Only https URLs on fixed gateway hosts are ever passed in."""
    if not url.startswith("https://"):
        raise GatewayError("Refusing a non-HTTPS gateway address.")
    data = None
    headers = dict(headers or {})
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    headers.setdefault("Accept", "application/json")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)  # nosec B310
    try:
        with _opener.open(request, timeout=timeout) as response:  # nosec B310
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    except TimeoutError:
        raise GatewayError("Connection timed out after %d seconds." % timeout)
    except urllib.error.URLError as exc:
        raise GatewayError("Could not reach the gateway (%s)." % getattr(exc, "reason", exc))
    try:
        return status, json.loads(raw or b"{}")
    except ValueError:
        return status, {}
