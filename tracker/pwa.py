"""Installable-app plumbing: a service worker and an offline page.

The worker caches only versioned static files (stale-while-revalidate) and one
public offline page. It never stores pages, API responses or anything else, so
no financial or personal data is ever kept on the device by Wealtharr.
"""
from hashlib import sha256

from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

WORKER = """/* Wealtharr service worker (%(version)s). Caches static files only. */
const STATIC = "wealtharr-static-%(version)s";
const OFFLINE = "/offline/";

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(STATIC).then((cache) => cache.add(OFFLINE)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys
        .filter((key) => key.startsWith("wealtharr-static-") && key !== STATIC)
        .map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.open(STATIC).then((cache) => cache.match(request).then((hit) => {
        const fresh = fetch(request).then((response) => {
          if (response.ok) cache.put(request, response.clone());
          return response;
        }).catch(() => hit);
        return hit || fresh;
      }))
    );
  } else if (request.mode === "navigate") {
    // Pages are never cached: offline, show the generic page instead.
    event.respondWith(fetch(request).catch(() => caches.match(OFFLINE)));
  }
  // Anything else goes straight to the network, untouched.
});
"""


def _version():
    return sha256(WORKER.replace("%(version)s", "").encode()).hexdigest()[:10]


@require_GET
def service_worker(request):
    response = HttpResponse(WORKER % {"version": _version()},
                            content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache"
    return response


@require_GET
def offline(request):
    return render(request, "offline.html")
