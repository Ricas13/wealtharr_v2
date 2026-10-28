"""Readiness, request ids and JSON logs: what an operator needs to run this as a service."""
import contextvars
import json
import logging
import time
import uuid

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.http import JsonResponse
from django.views.decorators.http import require_GET

_request_id = contextvars.ContextVar("wealtharr_request_id", default="")


def current_request_id():
    return _request_id.get()


class RequestIdMiddleware:
    """Use the proxy's X-Request-ID (or make one), tag log lines with it, echo it back."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        rid = (request.META.get("HTTP_X_REQUEST_ID") or "").strip()[:64] or uuid.uuid4().hex[:16]
        request.META["HTTP_X_REQUEST_ID"] = rid  # the audit log reads it from here
        token = _request_id.set(rid)
        try:
            response = self.get_response(request)
        finally:
            _request_id.reset(token)
        response["X-Request-ID"] = rid
        return response


class RequestIdFilter(logging.Filter):
    def filter(self, record):
        record.request_id = current_request_id()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record):
        out = {"time": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + "Z",
               "level": record.levelname, "logger": record.name, "message": record.getMessage(),
               "request_id": getattr(record, "request_id", "")}
        if record.exc_info:
            out["exception"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def readiness():
    """{check: (ok, detail)}. Ready means: database answers, migrations applied, a default plan exists."""
    checks = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        checks["database"] = (True, "")
        executor = MigrationExecutor(connection)
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        checks["migrations"] = (not pending, "%d unapplied" % len(pending) if pending else "")
        from tenancy.models import Plan
        has_default = Plan.objects.filter(is_default=True).exists()
        checks["default_plan"] = (has_default, "" if has_default else "no default plan")
    except Exception as exc:  # any failure means not ready; the reason is for the operator
        checks.setdefault("database", (False, type(exc).__name__))
    return checks


@require_GET
def ready(request):
    checks = readiness()
    ok = all(good for good, _ in checks.values())
    return JsonResponse({"status": "ready" if ok else "not ready",
                         "checks": {k: {"ok": g, "detail": d} for k, (g, d) in checks.items()}},
                        status=200 if ok else 503)
