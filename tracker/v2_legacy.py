"""Read-only permanent bookmarks from PR #11 to the single v2 UI.

Only GET/HEAD redirects. Replaying legacy financial POST payloads via a 301
could record unintended trades; all retired write endpoints return 405.
"""
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseNotAllowed, HttpResponsePermanentRedirect
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from .models import StrategyInstance


def owner_instances(user):
    return StrategyInstance.objects.filter(
        owner=user, account__owner=user
    ).exclude(account__kind="PAPER").order_by("created_at", "pk")


def preferred_url(user, section="today"):
    ids = list(owner_instances(user).values_list("pk", flat=True)[:2])
    if len(ids) == 1:
        return reverse("v2_instance_page", args=[ids[0], section])
    return reverse("v2_overview")


def _to(url):
    return HttpResponsePermanentRedirect(url)


@login_required
@require_http_methods(["GET", "HEAD"])
def entry(request):
    return _to(preferred_url(request.user))


@login_required
def cockpit_section(request, section="today", signal_id=None):
    if request.method not in ("GET", "HEAD"):
        return HttpResponseNotAllowed(["GET", "HEAD"])
    if section == "settings":
        return _to(reverse("v2_settings"))
    if section == "start":
        return _to(reverse("v2_add_strategy"))
    if section in ("prices", "deposit"):
        section = "today" if section == "prices" else "ledger"
    if section == "import":
        ids = list(owner_instances(request.user).values_list("pk", flat=True)[:2])
        return _to(reverse("v2_ledger_import", args=[ids[0]]) if len(ids) == 1
                   else reverse("v2_overview"))
    if section == "rebalance":
        # The historic link identifies a specific saved signal. Its owner and
        # strategy must be checked even if the user now has several accounts.
        # Never send a foreign or missing signal to a generic owner page.
        from django.shortcuts import get_object_or_404
        from .models import StrategySignal
        signal = get_object_or_404(
            StrategySignal.objects.filter(
                strategy__owner=request.user,
                strategy__account__owner=request.user,
            ).exclude(strategy__account__kind="PAPER"),
            pk=signal_id, kind="QUARTERLY",
        )
        return _to(reverse("v2_guided_review", args=[signal.strategy_id, signal.pk]))
    if section not in ("today", "history", "ledger", "strategy", "breakdown"):
        raise Http404
    return _to(preferred_url(request.user, section))


@login_required
def app_section(request, section="", instance_id=None, signal_id=None):
    # Even deprecated POST paths must not reveal whether another owner's
    # strategy exists. Check ownership before returning 405 for retired writes.
    if instance_id is not None and not owner_instances(request.user).filter(pk=instance_id).exists():
        raise Http404
    if request.method not in ("GET", "HEAD"):
        return HttpResponseNotAllowed(["GET", "HEAD"])
    if section == "settings":
        return _to(reverse("v2_settings"))
    if section in ("add", "start"):
        return _to(reverse("v2_add_strategy"))
    if section == "control":
        return _to(reverse("control"))
    if instance_id is None:
        return _to(reverse("v2_overview"))
    if not owner_instances(request.user).filter(pk=instance_id).exists():
        raise Http404
    if section == "review" and signal_id is not None:
        from .models import StrategySignal
        if StrategySignal.objects.filter(pk=signal_id, strategy_id=instance_id,
                                          strategy__owner=request.user).exists():
            return _to(reverse("v2_guided_review", args=[instance_id, signal_id]))
        raise Http404
    section = {
        "": "strategy", "ledger": "ledger", "price": "today",
        "monitor": "strategy", "refresh": "today",
        "rebaseline": "breakdown", "signal": "breakdown",
    }.get(section, section)
    if section not in ("today", "history", "ledger", "strategy", "breakdown"):
        raise Http404
    return _to(reverse("v2_instance_page", args=[instance_id, section]))
