import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import Count
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from django.utils import timezone
from .forms import AccountForm, IntegrationForm, LedgerForm, NotificationForm, PriceForm, StrategyForm
from .models import Account, LedgerEntry, NotificationPreference, PriceMark, StrategyInstance, StrategyPreset, SystemIntegration, WealthSnapshot
from .services import consolidated, dispatch, ledger_summary, validate_and_record

@require_GET
def home(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    return render(request, "home.html")

def turnstile_verified(request):
    if not settings.TURNSTILE_REQUIRED:
        return True
    token = request.POST.get("cf-turnstile-response", "")
    if not settings.TURNSTILE_SECRET_KEY or not settings.TURNSTILE_SITE_KEY or not token:
        return False
    body = urlencode({"secret": settings.TURNSTILE_SECRET_KEY, "response": token}).encode("ascii")
    try:
        req = Request("https://challenges.cloudflare.com/turnstile/v0/siteverify", data=body, method="POST")
        with urlopen(req, timeout=8) as result:
            return json.load(result).get("success") is True
    except Exception:
        return False

def sign_in(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = AuthenticationForm(request=request, data=request.POST or None)
    if request.method == "POST":
        valid = form.is_valid()
        turnstile = turnstile_verified(request)
        if valid and turnstile:
            login(request, form.get_user())
            return redirect("dashboard")
        if not turnstile:
            form.add_error(None, "Security verification failed. Please try again.")
    return render(request, "login.html", {"form": form, "turnstile_site_key": settings.TURNSTILE_SITE_KEY,
                                           "turnstile_required": settings.TURNSTILE_REQUIRED})

@login_required
@require_POST
def sign_out(request):
    logout(request)
    return redirect("home")

@login_required
def dashboard(request):
    data = consolidated(request.user)
    snapshots = WealthSnapshot.objects.filter(owner=request.user).order_by("as_of")
    chart = [{"date": s.as_of.isoformat(), "value": float(s.total_value),
              "contributed": float(s.net_contributed)} for s in snapshots]
    data.update(chart=chart, accounts=Account.objects.filter(owner=request.user).annotate(strategy_count=Count("strategies")))
    return render(request, "dashboard.html", data)

@login_required
def accounts(request):
    form = AccountForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.owner = request.user
        try:
            obj.full_clean()
            obj.save()
            messages.success(request, "Account created. You can now add a strategy to it.")
            return redirect("new_strategy")
        except ValidationError as exc:
            form.add_error(None, exc)
    return render(request, "accounts.html", {"form": form, "accounts": Account.objects.filter(owner=request.user).annotate(strategy_count=Count("strategies"))})

@login_required
def new_strategy(request):
    if not Account.objects.filter(owner=request.user).exists():
        messages.info(request, "Create an investment account first.")
        return redirect("accounts")
    form = StrategyForm(request.POST or None, owner=request.user)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.owner = request.user
        try:
            obj.full_clean()
            obj.save()
            messages.success(request, "Strategy tab created. Record your starting deposit to begin tracking.")
            return redirect("strategy_detail", pk=obj.pk)
        except ValidationError as exc:
            form.add_error(None, exc)
    return render(request, "new_strategy.html", {"form": form, "presets": StrategyPreset.objects.filter(active=True)})

def render_strategy(request, strategy, entry_form=None, price_form=None):
    report = ledger_summary(strategy)
    holdings = []
    for symbol, qty in sorted(report["positions"].items()):
        mark = report["marks"].get(symbol)
        holdings.append({"symbol": symbol, "quantity": qty, "mark": mark, "value": (qty * mark.price) if mark else None})
    entries = strategy.ledger.all().order_by("-occurred_on", "-pk")[:60]
    return render(request, "strategy.html", {
        "strategy": strategy, "report": report, "holdings": holdings,
        "entries": entries,
        "entry_form": entry_form or LedgerForm(), "price_form": price_form or PriceForm(),
    })

@login_required
def strategy_detail(request, pk):
    strategy = get_object_or_404(StrategyInstance.objects.select_related("account", "preset"), pk=pk, owner=request.user)
    return render_strategy(request, strategy)

@login_required
@require_POST
def record_entry(request, pk):
    strategy = get_object_or_404(StrategyInstance, pk=pk, owner=request.user)
    form = LedgerForm(request.POST)
    if form.is_valid():
        entry = form.save(commit=False)
        try:
            validate_and_record(strategy, entry)
            messages.success(request, "Transaction recorded. Values update from your actual ledger.")
            return redirect("strategy_detail", pk=pk)
        except ValidationError as exc:
            form.add_error(None, exc)
    return render_strategy(request, strategy, entry_form=form)

@login_required
@require_POST
def record_price(request, pk):
    strategy = get_object_or_404(StrategyInstance, pk=pk, owner=request.user)
    form = PriceForm(request.POST)
    if form.is_valid():
        summary = ledger_summary(strategy)
        symbol = form.cleaned_data["symbol"]
        if summary["positions"].get(symbol, 0) <= 0:
            form.add_error("symbol", "This strategy does not currently hold that ticker.")
        else:
            previous = PriceMark.objects.filter(strategy=strategy, symbol=symbol).first()
            if previous and form.cleaned_data["as_of"] < previous.as_of:
                form.add_error("as_of", "Use a price date on or after the existing mark.")
            else:
                PriceMark.objects.update_or_create(strategy=strategy, symbol=symbol, defaults={
                    "price": form.cleaned_data["price"], "as_of": form.cleaned_data["as_of"],
                })
                messages.success(request, "Price saved. Record a wealth snapshot on the overview to add a chart point.")
                return redirect("strategy_detail", pk=pk)
    return render_strategy(request, strategy, price_form=form)

@login_required
@require_POST
def capture_snapshot(request):
    data = consolidated(request.user)
    if not data["items"]:
        messages.error(request, "Create a strategy before capturing wealth.")
    elif data["total"] is None:
        messages.error(request, "Add a current GBP price for every held ticker before capturing wealth.")
    else:
        WealthSnapshot.objects.update_or_create(owner=request.user, as_of=timezone.localdate(), defaults={
            "total_value": data["total"], "net_contributed": data["net_contributed"],
        })
        messages.success(request, "Today's actual portfolio valuation saved to your wealth chart.")
    return redirect("dashboard")

@login_required
def user_settings(request):
    pref, _ = NotificationPreference.objects.get_or_create(owner=request.user, defaults={"destination_email": request.user.email})
    form = NotificationForm(request.POST or None, instance=pref)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Your notification preferences are saved.")
        return redirect("user_settings")
    return render(request, "settings.html", {"form": form})

@login_required
def change_password(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    for f in form.fields.values():
        f.widget.attrs["class"] = "field"
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        messages.success(request, "Password updated. Other sessions may need to sign in again.")
        return redirect("user_settings")
    return render(request, "password.html", {"form": form})

@staff_member_required
def control(request):
    cfg = SystemIntegration.current()
    form = IntegrationForm(request.POST or None, instance=cfg)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Integrations saved. Secrets are encrypted in the database.")
        return redirect("control")
    return render(request, "control.html", {
        "form": form, "cfg": cfg,
        "smtp_password_set": bool(cfg.smtp_password_ciphertext),
        "discord_set": bool(cfg.discord_url_ciphertext),
        "turnstile_set": bool(settings.TURNSTILE_SITE_KEY and settings.TURNSTILE_SECRET_KEY),
        "turnstile_required": settings.TURNSTILE_REQUIRED,
    })

@staff_member_required
@require_POST
def test_notification(request, channel):
    if channel not in ("email", "discord"):
        return HttpResponse(status=404)
    from uuid import uuid4
    result = dispatch(request.user, "manual-" + uuid4().hex, "Wealtharr test notification",
                      "Your Wealtharr notification channel is connected.", channels=(channel,))
    if result.get(channel) == "sent":
        messages.success(request, channel.title() + " test sent.")
    else:
        messages.error(request, channel.title() + " test: " + result.get(channel, "failed") + ". Check your user preferences and integration configuration.")
    return redirect("control")

@require_GET
def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return JsonResponse({"status": "ok"})
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
