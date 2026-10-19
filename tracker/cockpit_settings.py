"""User-facing owner notification setup reusing encrypted integrations.

One-user deployment: SystemIntegration is still a global singleton. Never
allow an ordinary user to write/read its transport configuration.
"""
from uuid import uuid4
from datetime import timedelta
from django.utils import timezone
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST
from .forms import NotificationForm, IntegrationForm
from .models import (
    NotificationDelivery, NotificationPreference, NotificationSchedulerRun,
    SystemIntegration,
)
from .notification_routing import CATEGORIES
from .services import dispatch


@login_required
@require_http_methods(['GET','POST'])
def owner_settings(request):
    pref, _ = NotificationPreference.objects.get_or_create(owner=request.user,
                                defaults={'destination_email': request.user.email})
    integration = SystemIntegration.current() if request.user.is_staff else None
    pref_form = NotificationForm(request.POST if request.POST.get('save') == 'preferences' else None,
                                 instance=pref, prefix='notify')
    transport_form = IntegrationForm(request.POST if request.POST.get('save') == 'transport' else None,
                                      instance=integration, prefix='transport') if integration else None
    if request.method == 'POST':
        if request.POST.get('save') == 'preferences' and pref_form.is_valid():
            pref_form.save()
            messages.success(request, 'Notification preferences saved.')
            return redirect('cockpit_settings')
        if request.POST.get('save') == 'transport' and integration and transport_form.is_valid():
            transport_form.save()
            messages.success(request, 'Email and Discord transport saved securely.')
            return redirect('cockpit_settings')
    routes = tuple({
        "key": key, "title": title,
        "email": pref_form[key + "_email"],
        "discord": pref_form[key + "_discord"],
    } for key, title in CATEGORIES)
    history = NotificationDelivery.objects.filter(
        owner=request.user).order_by("-created_at", "-pk")
    last_scheduler = (NotificationSchedulerRun.objects.order_by(
        "-started_at", "-pk").first() if request.user.is_staff else None)
    if not last_scheduler:
        scheduler_state = "No scheduler run recorded"
    elif last_scheduler.status == "FAILED":
        scheduler_state = "Last scheduler run failed"
    elif last_scheduler.status == "RUNNING" and last_scheduler.started_at < timezone.now() - timedelta(hours=1):
        scheduler_state = "Scheduler run appears interrupted"
    elif last_scheduler.started_at < timezone.now() - timedelta(hours=3):
        scheduler_state = "Scheduler heartbeat is overdue"
    elif last_scheduler.status == "RUNNING":
        scheduler_state = "Scheduler is running"
    else:
        scheduler_state = "Scheduler recently completed successfully"
    return render(request, "settings_cockpit.html", {
        "pref_form": pref_form, "route_rows": routes,
        "transport_form": transport_form,
        "discord_configured": bool(integration and integration.discord_url_ciphertext),
        "smtp_configured": bool(integration and integration.smtp_host and integration.smtp_sender),
        "recent_deliveries": history[:12],
        "unsent_count": history.filter(status__in=("FAILED", "PENDING", "SENDING")).count(),
        "scheduler_runs": (NotificationSchedulerRun.objects.order_by("-started_at")[:8]
                            if request.user.is_staff else ()),
        "scheduler_state": scheduler_state if request.user.is_staff else "",
    })


@staff_member_required
@require_POST
def test_delivery(request, channel):
    if channel not in ('email','discord'):
        messages.error(request, 'Unsupported notification channel.')
        return redirect('cockpit_settings')
    # User-initiated only. Never return the webhook or SMTP password in HTML.
    outcome = dispatch(request.user, 'test:'+uuid4().hex, 'Wealtharr test',
        'This confirms that your private 9Sig alerts can reach this destination.',
        channels=(channel,))
    if outcome.get(channel) == 'sent':
        messages.success(request, channel.title() + ' test delivered.')
    else:
        messages.error(request, channel.title() + ' test unavailable: check destination and transport settings.')
    return redirect('cockpit_settings')
