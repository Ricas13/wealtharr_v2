from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from . import billing, gateways
from .gateway_http import GatewayError
from .access import _resolve
from .models import Plan


@csrf_exempt
@require_POST
def stripe_webhook(request):
    status, text = billing.process(request.body, request.headers.get("Stripe-Signature", ""))
    return HttpResponse(text, status=status, content_type="text/plain")


@csrf_exempt
@require_POST
def paypal_webhook(request):
    status, text = billing.process_paypal(request.headers, request.body.decode("utf-8", "replace"))
    return HttpResponse(text, status=status, content_type="text/plain")


@login_required
@require_POST
def checkout(request, slug, provider):
    plan = get_object_or_404(Plan, slug=slug, is_public=True)
    if provider not in gateways.PROVIDERS:
        return redirect("plans")
    base = request.build_absolute_uri(reverse("plans_return"))
    try:
        url = gateways.start_checkout(provider, request.user, plan, base + "?paid=1", base + "?paid=0")
    except GatewayError as exc:
        messages.error(request, "Could not start checkout: %s" % exc)
        return redirect("plans")
    from . import audit
    audit.record("billing.checkout_started", actor=request.user, request=request,
                 metadata={"plan": plan.slug, "provider": provider})
    return redirect(url)


@login_required
@require_GET
def plans_return(request):
    if request.GET.get("paid") == "1":
        messages.success(request, "Thank you. Your plan changes as soon as the payment provider confirms it, usually within a minute.")
    else:
        messages.info(request, "Checkout was cancelled. Nothing was charged.")
    return redirect("plans")


@login_required
@require_GET
def plans_page(request):
    from tracker.v2_views import _render
    current, _ = _resolve(request.user)
    offers = []
    for plan in Plan.objects.filter(is_public=True):
        link = plan.stripe_link
        if link and plan.stripe_payment_link_id:
            # Stripe hands this back on the paid checkout so the plan can be assigned.
            link += ("&" if "?" in link else "?") + f"client_reference_id={request.user.pk}"
        sellable = gateways.sellable_providers(plan)
        offers.append({"plan": plan, "stripe": link, "paypal": plan.paypal_link,
                       "checkout": [{"provider": p, "label": gateways.LABELS[p]} for p in sellable],
                       "stripe_api": "stripe" in sellable, "paypal_api": "paypal" in sellable,
                       "current": current is not None and plan.pk == current.pk,
                       "automatic": bool(sellable) or bool(plan.stripe_payment_link_id)})
    return _render(request, page_key="plans", page_title="Plans", current_plan=current, offers=offers)


def _platform_admin(view):
    from django.contrib.auth.decorators import user_passes_test
    return user_passes_test(lambda u: u.is_active and u.is_superuser, login_url="/login/")(view)


@_platform_admin
@require_POST
def gateway_test(request, provider):
    if provider not in gateways.PROVIDERS:
        return redirect("control")
    ok, message = gateways.test_connection(provider)
    (messages.success if ok else messages.error)(request, "%s: %s" % (gateways.LABELS[provider], message))
    return redirect(reverse("control") + "#" + provider)


@_platform_admin
@require_POST
def gateway_use_environment(request, provider):
    if provider in gateways.PROVIDERS:
        gateways.use_environment(provider, actor=request.user, request=request)
        messages.success(request, "%s now uses the server's settings." % gateways.LABELS[provider])
    return redirect(reverse("control") + "#" + provider)
