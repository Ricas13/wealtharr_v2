"""Private owner Strategy and Settings pages, fixed rules and safe configuration.

No route can edit rule parameters, immutable account identity or instrument
mapping. Secrets and transport URLs never enter any GET context or export.
"""
import csv
import io
import json
import os
import zipfile
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import ProtectedError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .core.catalogue import rows_for, CATALOGUE_VERSION
from .forms import IntegrationForm, NotificationForm
from .models import (
    Account, BrokerImportRow, InstanceOpeningSnapshot, LedgerEntry,
    LedgerRevision, NotificationDelivery, NotificationPreference,
    NotificationSchedulerRun, OpeningReconciliation, RebalanceExecution,
    SignalFill, StrategyBaseline, StrategyInstance, StrategySignal,
    StrategyValueSnapshot, SystemIntegration,
)
from .notification_routing import CATEGORIES, CHANNELS
from .services import dispatch
from .strategies.registry import describe_strategy
from .v2_views import _render


# Display label mapping: an "unacted" wizard choice controls followup delivery.
CATEGORIES_UI = (
    ("quarterly", "Quarter-end action"),
    ("urgent", "Urgent strategy warning"),
    ("unacted", "Reminder if I have not acted"),
    ("contribution", "Planned contribution reminder"),
    ("data", "Data problem close to review"),
)
ALERT_KEYS = tuple(row[0] for row in CATEGORIES_UI)


def _owned(request, instance_id):
    return get_object_or_404(
        StrategyInstance.objects.select_related("account", "preset")
        .filter(owner=request.user, account__owner=request.user)
        .exclude(account__kind="PAPER"),
        pk=instance_id,
    )


def _routes(strategy):
    configured = strategy.alert_routes or {}
    result = []
    for key, label in CATEGORIES_UI:
        values = configured.get(key, {})
        # Legacy instances use account-level categories until opted into a
        # per-instance matrix, to preserve existing notification behaviour.
        legacy = not strategy.strategy_key and not configured
        result.append({
            "key": key, "label": label,
            "email": values.get("email", legacy),
            "discord": values.get("discord", legacy),
        })
    return tuple(result)


def _rule_history(instance):
    """Display saved version *evidence*, not fabricated upgrade history."""
    reset_rows = list(instance.baselines.order_by("-as_of", "-pk")[:12])
    versions = []
    if instance.strategy_version or instance.mapping_version:
        versions.append({
            "title": "Pinned at enrolment",
            "date": instance.started_on or instance.created_at.date(),
            "strategy": instance.strategy_version or "Not pinned",
            "mapping": instance.mapping_version or "Not pinned",
        })
    for baseline in reset_rows:
        versions.append({
            "title": "Owner-confirmed baseline",
            "date": baseline.as_of,
            "strategy": baseline.strategy_version or "Not recorded",
            "mapping": baseline.mapping_version or "Not recorded",
        })
    return tuple(versions)


@login_required
@require_http_methods(["GET", "POST"])
def strategy_page(request, instance_id):
    instance = _owned(request, instance_id)
    if request.method == "POST":
        if request.POST.get("intent") != "alerts":
            raise Http404("Unknown action")
        if not instance.strategy_key:
            messages.error(request, "Legacy strategy routing remains account-wide.")
            return redirect("v2_instance_page", instance_id=instance.pk, section="strategy")
        routes = {
            key: {
                channel: request.POST.get(f"{key}_{channel}") == "on"
                for channel in CHANNELS
            } for key in ALERT_KEYS
        }
        # This is notification policy only. Never touch the pinned rule,
        # account, instrument roles, or tracking-only flags.
        instance.alert_routes = routes
        instance.save(update_fields=["alert_routes"])
        messages.success(request, "Alert choices saved for this strategy only.")
        return redirect("v2_instance_page", instance_id=instance.pk, section="strategy")
    name = instance.strategy_key or instance.preset.slug
    description = describe_strategy(name)
    lines = rows_for(
        name, instance.account.broker, instance.account.kind,
        country=instance.account.country,
    )
    return _render(
        request, page_key="instance", page_title="Strategy",
        instance=instance, section="strategy",
        strategy_description=description, strategy_catalogue_rows=lines,
        strategy_routes=_routes(instance), strategy_history=_rule_history(instance),
        strategy_tracking_only=instance.tracking_only,
    )


def _heartbeat():
    latest = NotificationSchedulerRun.objects.order_by("-started_at", "-pk").first()
    succeeded = NotificationSchedulerRun.objects.filter(
        status="SUCCEEDED", finished_at__isnull=False
    ).order_by("-finished_at").first()
    cutoff = timezone.now() - timedelta(hours=3)
    status = (
        "No successful run recorded" if not succeeded
        else "Last success overdue" if succeeded.finished_at < cutoff
        else "Recent successful run"
    )
    if latest and latest.status == "FAILED":
        status = "Latest scheduler run failed"
    if latest and latest.status == "RUNNING" and latest.started_at < cutoff:
        status = "Scheduler appears interrupted"
    return {
        "status": status, "last_status": latest.status if latest else "Never run",
        "last_started": latest.started_at if latest else None,
        "last_finished": succeeded.finished_at if succeeded else None,
        "new_deliveries": latest.new_deliveries if latest else 0,
        "retry_attempts": latest.attempted_retries if latest else 0,
        "last_error": latest.error_code if latest else "",
        "stale": not succeeded or succeeded.finished_at < cutoff,
    }


def _session_count(user):
    # Decode signed server-side session blobs, never display session keys.
    found = 0
    for row in Session.objects.filter(expire_date__gt=timezone.now()):
        try:
            if row.get_decoded().get("_auth_user_id") == str(user.pk):
                found += 1
        except Exception:
            continue
    return found


def _backup_info():
    # Diagnostics are not proof of restore; do not expose paths or filenames.
    root = os.environ.get("WEALTHARR_BACKUP_DIR", "")
    if not root:
        return {"label": "No backup directory reported", "latest": None}
    try:
        folder = Path(root)
        if not folder.is_dir():
            return {"label": "Backup directory unavailable", "latest": None}
        candidates = [
            p.stat().st_mtime for p in folder.iterdir()
            if p.is_file() and p.suffix in (".gz", ".dump", ".sql", ".age")
        ]
        if not candidates:
            return {"label": "No backup artifact found", "latest": None}
        return {"label": "Backup artifact present — restore not verified",
                "latest": timezone.datetime.fromtimestamp(
                    max(candidates), tz=timezone.get_current_timezone())}
    except (OSError, ValueError):
        return {"label": "Backup status unavailable", "latest": None}


def _export_owner(owner):
    """Export exact owner ledger and provenance; no credentials or session IDs."""
    instances = StrategyInstance.objects.filter(
        owner=owner, account__owner=owner
    ).select_related("account", "preset")
    ids = list(instances.values_list("pk", flat=True))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        def table(name, columns, rows):
            data = io.StringIO(newline="")
            writer = csv.writer(data)
            writer.writerow(columns)
            for row in rows:
                writer.writerow([
                    ("'" + v if isinstance(v, str) and
                     v.lstrip().startswith(("=", "+", "-", "@")) else v)
                    for v in row
                ])
            out.writestr(name, data.getvalue())
        table("strategies.csv",
              ("id", "account", "kind", "broker", "name", "strategy_key",
               "strategy_version", "mapping_version", "started_on", "tracking_only"),
              ((x.pk, x.account.name, x.account.kind, x.account.broker, x.name,
                x.strategy_key, x.strategy_version, x.mapping_version,
                x.started_on or "", x.tracking_only) for x in instances))
        table("ledger.csv",
              ("id", "instance", "date", "kind", "symbol", "quantity",
               "unit_price_gbp", "cash_amount_gbp", "fee_gbp", "note"),
              LedgerEntry.objects.filter(strategy_id__in=ids).values_list(
                  "pk", "strategy_id", "occurred_on", "kind", "symbol",
                  "quantity", "unit_price", "amount", "fee", "note"))
        table("revisions.csv",
              ("id", "instance", "entry", "version", "operation", "before", "after", "reason"),
              ((x.pk, x.strategy_id, x.entry_id, x.version, x.operation,
                json.dumps(x.before, sort_keys=True) if x.before else "",
                json.dumps(x.after, sort_keys=True) if x.after else "", x.reason)
               for x in LedgerRevision.objects.filter(strategy_id__in=ids)))
        table("signals.csv",
              ("id", "instance", "date", "kind", "title", "outcome", "signal_key"),
              StrategySignal.objects.filter(strategy_id__in=ids).values_list(
                  "pk", "strategy_id", "as_of", "kind", "title",
                  "review_outcome", "signal_key"))
        table("valuations.csv",
              ("id", "instance", "date", "GBP_value", "GBP_contributed"),
              StrategyValueSnapshot.objects.filter(strategy_id__in=ids).values_list(
                  "pk", "strategy_id", "as_of", "total_value", "net_contributed"))
        table("baselines.csv",
              ("id", "instance", "date", "strategy_version", "mapping_version",
               "equity_gbp", "safe_gbp", "cash_gbp"),
              StrategyBaseline.objects.filter(strategy_id__in=ids).values_list(
                  "pk", "strategy_id", "as_of", "strategy_version",
                  "mapping_version", "equity_value", "safe_value", "cash_value"))
        out.writestr("README.txt",
                     "Wealtharr private owner data export. Real recorded data only.\\n"
                     "Secrets, passwords and session tokens intentionally excluded.\\n"
                     "CSV formulas neutralized; verify original source documents.\\n")
    resp = HttpResponse(buf.getvalue(), content_type="application/zip")
    resp["Content-Disposition"] = 'attachment; filename="wealtharr-owner-export.zip"'
    resp["Cache-Control"] = "no-store"
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


@require_http_methods(["GET", "POST"])
@login_required
def settings_page(request):
    pref, _ = NotificationPreference.objects.get_or_create(
        owner=request.user, defaults={"destination_email": request.user.email})
    transport = SystemIntegration.current() if request.user.is_staff else None
    form = NotificationForm(instance=pref)
    transport_form = IntegrationForm(instance=transport) if transport else None
    if request.method == "POST":
        intent = request.POST.get("intent", "")
        if intent == "preferences":
            form = NotificationForm(request.POST, instance=pref)
            if form.is_valid():
                form.save()
                messages.success(request, "Notification preferences saved.")
                return redirect("v2_settings")
        elif intent == "transport" and request.user.is_staff and transport:
            transport_form = IntegrationForm(request.POST, instance=transport)
            if transport_form.is_valid():
                transport_form.save()
                messages.success(request, "Encrypted SMTP and Discord configuration saved.")
                return redirect("v2_settings")
        else:
            raise Http404("Unknown settings action")
    info = _heartbeat() if request.user.is_staff else None
    deliv = NotificationDelivery.objects.filter(owner=request.user).order_by("-created_at")[:12]
    return _render(
        request, page_key="settings", page_title="Settings",
        settings_form=form, settings_transport=transport_form,
        settings_smtp_set=bool(transport and transport.smtp_host and transport.smtp_sender),
        settings_discord_set=bool(transport and transport.discord_url_ciphertext),
        settings_heartbeat=info,
        settings_backup=_backup_info() if request.user.is_staff else None,
        settings_deliveries=deliv,
        settings_sessions=_session_count(request.user),
        settings_isa_enabled=pref.isa_allowance_enabled,
        settings_categories=CATEGORIES,
    )


@require_POST
@login_required
def test_channel(request, channel):
    if not request.user.is_staff or channel not in CHANNELS:
        raise Http404
    outcome = dispatch(
        request.user, "test:"+uuid4().hex,
        "Wealtharr delivery test",
        "Private notification destination test. No financial instruction.",
        channels=(channel,),
    )
    if outcome.get(channel) == "sent":
        messages.success(request, "Test delivered to " + channel + ".")
    else:
        messages.error(request, "Test could not be delivered. Check preferences and transport.")
    return redirect("v2_settings")


@require_POST
@login_required
def export_data(request):
    if not request.user.check_password(request.POST.get("password", "")):
        messages.error(request, "Password confirmation required for private export.")
        return redirect("v2_settings")
    return _export_owner(request.user)


@require_POST
@login_required
def revoke_sessions(request):
    if not request.user.check_password(request.POST.get("password", "")):
        messages.error(request, "Password confirmation required to sign out other sessions.")
        return redirect("v2_settings")
    changed = 0
    for record in Session.objects.filter(expire_date__gt=timezone.now()):
        if record.session_key == request.session.session_key:
            continue
        try:
            belongs = record.get_decoded().get("_auth_user_id") == str(request.user.pk)
        except Exception:
            belongs = False
        if belongs:
            record.delete()
            changed += 1
    messages.success(request, "Signed out %s other session(s)." % changed)
    return redirect("v2_settings")


@require_POST
@login_required
def reset_empty(request):
    """Never destroy a real financial audit trail in the web interface."""
    if not request.user.check_password(request.POST.get("password", "")) or (
            request.POST.get("confirmation") != "ERASE MY EMPTY STRATEGIES"):
        messages.error(request, "Correct password and typed confirmation are required.")
        return redirect("v2_settings")
    with transaction.atomic():
        owned = StrategyInstance.objects.select_for_update().filter(
            owner=request.user, account__owner=request.user)
        ids = list(owned.values_list("pk", flat=True))
        audited = (
            LedgerEntry.objects.filter(strategy_id__in=ids).exists() or
            LedgerRevision.objects.filter(strategy_id__in=ids).exists() or
            StrategySignal.objects.filter(strategy_id__in=ids).exists() or
            InstanceOpeningSnapshot.objects.filter(strategy_id__in=ids).exists() or
            NotificationDelivery.objects.filter(strategy_id__in=ids).exists()
        )
        if audited:
            messages.error(request, "Financial or delivery history exists. Reset refused; export and use the audited administrator recovery workflow.")
            return redirect("v2_settings")
        count = owned.count()
        try:
            owned.delete()
            Account.objects.filter(owner=request.user, strategies__isnull=True).delete()
        except ProtectedError:
            messages.error(request, "Existing protected records prevent deletion; no changes were saved.")
            return redirect("v2_settings")
    messages.success(request, "%s empty strategies removed. All financial audit records preserved." % count)
    return redirect("v2_settings")
