from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from . import billing
from .access import _resolve
from .models import Plan


@csrf_exempt
@require_POST
def stripe_webhook(request):
    status, text = billing.process(request.body, request.headers.get("Stripe-Signature", ""))
    return HttpResponse(text, status=status, content_type="text/plain")


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
        offers.append({"plan": plan, "stripe": link, "paypal": plan.paypal_link,
                       "current": current is not None and plan.pk == current.pk,
                       "automatic": bool(plan.stripe_payment_link_id)})
    return _render(request, page_key="plans", page_title="Plans", current_plan=current, offers=offers)
