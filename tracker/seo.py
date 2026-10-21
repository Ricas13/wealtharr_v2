"""Search-engine plumbing for the public pages only.

Public pages (homepage and guides) get canonical URLs on SITE_URL, a sitemap
and structured data. Everything else — the private app, login, JSON and
forms — is marked noindex and disallowed in robots.txt.
"""
from datetime import date

from django.conf import settings
from django.http import HttpResponse, HttpResponsePermanentRedirect
from django.urls import reverse
from django.views.decorators.cache import cache_page
from django.views.decorators.http import require_GET

from .guides import GUIDES

# Robots never index the private app, sign-in, admin or form/JSON endpoints.
PRIVATE_PREFIXES = (
    "/s/", "/overview/", "/settings/", "/add-strategy/", "/login/", "/logout/",
    "/security/", "/control/", "/django-admin/", "/invite/", "/compare.json", "/health/",
)
SITE_UPDATED = date(2026, 10, 21)


def site_url(request=None):
    """The one public origin, e.g. https://wealtharr.com (no trailing slash)."""
    if settings.SITE_URL:
        return settings.SITE_URL
    return request.build_absolute_uri("/").rstrip("/") if request else ""


def public_paths():
    return ["/", reverse("guides")] + [reverse("guide", args=[g["slug"]]) for g in GUIDES]


def is_public(path):
    return path in set(public_paths()) or path in ("/robots.txt", "/sitemap.xml", "/site.webmanifest")


class SeoMiddleware:
    """301 to SITE_URL's host, and noindex everything that isn't public."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        target = settings.SITE_URL
        if (target and request.method in ("GET", "HEAD")
                and request.path_info != "/health/"
                and "://" in target and request.get_host() != target.split("://", 1)[1]):
            return HttpResponsePermanentRedirect(target + request.get_full_path())
        response = self.get_response(request)
        if not request.path_info.startswith("/static/") and not is_public(request.path_info):
            response["X-Robots-Tag"] = "noindex, nofollow"
        return response


@require_GET
@cache_page(3600)
def robots_txt(request):
    lines = ["User-agent: *", "Allow: /"]
    lines += ["Disallow: %s" % prefix for prefix in PRIVATE_PREFIXES]
    lines += ["", "Sitemap: %s/sitemap.xml" % site_url(request)]
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain")


@require_GET
@cache_page(3600)
def sitemap_xml(request):
    base = site_url(request)
    updated = {"/": SITE_UPDATED, reverse("guides"): max(g["updated"] for g in GUIDES)}
    for guide in GUIDES:
        updated[reverse("guide", args=[guide["slug"]])] = guide["updated"]
    rows = "".join(
        "<url><loc>%s%s</loc><lastmod>%s</lastmod></url>" % (base, path, updated[path].isoformat())
        for path in public_paths())
    body = ('<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">%s</urlset>' % rows)
    return HttpResponse(body, content_type="application/xml")


@require_GET
def web_manifest(request):
    from django.templatetags.static import static
    import json
    body = {
        "name": "Wealtharr", "short_name": "Wealtharr",
        "description": "Follow a fixed investing strategy without the spreadsheet.",
        "start_url": "/", "display": "standalone",
        "background_color": "#0d1014", "theme_color": "#0d1014",
        "icons": [{"src": static("img/icon-192.png"), "sizes": "192x192", "type": "image/png"},
                  {"src": static("img/icon-512.png"), "sizes": "512x512", "type": "image/png"}],
    }
    return HttpResponse(json.dumps(body), content_type="application/manifest+json")
