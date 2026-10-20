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
from .forms import AccountForm, AutomationForm, IntegrationForm, LedgerForm, NotificationForm, PriceForm, StrategyForm, RealNineSigSetupForm
from .models import Account, LedgerEntry, NotificationPreference, PriceMark, StrategyInstance, StrategyPreset, StrategySignal, SystemIntegration, WealthSnapshot, StrategyValueSnapshot
from .services import consolidated, dispatch, ledger_summary, validate_and_record
from .market_signals import review_strategy

@require_GET
def home(request):
    if request.user.is_authenticated:
        return redirect("cockpit_today")
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
        return redirect("cockpit_today")
    form = AuthenticationForm(request=request, data=request.POST or None)
    if request.method == "POST":
        valid = form.is_valid()
        turnstile = turnstile_verified(request)
        if valid and turnstile:
            login(request, form.get_user())
            return redirect("cockpit_today")
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
def start_real_9sig(request):
    """Legacy bookmark: route ALL new tracking to the one-screen ISA flow.

    Old GET/POST fields, including GIA and arbitrary names, must not create
    an alternative portfolio. Existing portfolios and ledgers are untouched.
    """
    return redirect("cockpit_setup")


@login_required
def dashboard(request):
    data = consolidated(request.user)
    snapshots = WealthSnapshot.objects.filter(owner=request.user).order_by("as_of")
    chart = [{"date": s.as_of.isoformat(), "value": float(s.total_value),
              "contributed": float(s.net_contributed)} for s in snapshots]
    # A simple gain-to-net-contributions ratio is not a time-weighted return.
    # Withdrawals make even that basic ratio misleading, so omit it.
    withdrawals = LedgerEntry.objects.filter(
        strategy__owner=request.user, kind=LedgerEntry.WITHDRAW,
    ).exclude(strategy__account__kind="PAPER").exists()
    data["gain_pct"] = (
        (data["gain"] / data["net_contributed"] * 100)
        if (data["gain"] is not None and data["net_contributed"] > 0 and not withdrawals)
        else None
    )
    # Tiny, source-grounded sparklines are drawn only from actual saved strategy
    # valuations. No returns, prices or daily points are interpolated.
    from .presentation import sparkline_path
    strategy_ids = [item["strategy"].pk for item in data["items"]]
    snapshots_by_strategy = {}
    for point in StrategyValueSnapshot.objects.filter(
        strategy_id__in=strategy_ids).order_by("as_of"):
        snapshots_by_strategy.setdefault(point.strategy_id, []).append(
            (point.as_of, point.total_value))
    for item in data["items"]:
        item["sparkline_path"] = sparkline_path(
            snapshots_by_strategy.get(item["strategy"].pk, []))
    data.update(
        chart=chart,
        accounts=Account.objects.filter(owner=request.user).exclude(
            kind="PAPER"
        ).annotate(strategy_count=Count("strategies")),
        open_signals=StrategySignal.objects.filter(
            strategy__owner=request.user, acknowledged_at__isnull=True
        ).exclude(strategy__account__kind="PAPER").select_related(
            "strategy"
        ).order_by("-created_at")[:8],
    )
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
    return render(request, "accounts.html", {"form": form, "accounts": Account.objects.filter(owner=request.user).exclude(kind="PAPER").annotate(strategy_count=Count("strategies"))})

@login_required
def new_strategy(request):
    """Legacy general preset editor is superseded for this owner-only app."""
    return redirect("cockpit_setup")


def _monitoring_status(strategy):
    """Browser-safe monitoring diagnostics; never include integration credentials."""
    from datetime import timedelta
    from .models import YahooSync
    if not strategy.market_data_enabled:
        return {"active": False, "message": "Price monitoring is switched off."}
    symbols = [strategy.equity_symbol, strategy.safe_symbol, "TQQQ", "SPY"]
    rows = {x.symbol: x for x in YahooSync.objects.filter(symbol__in=symbols)}
    missing = [symbol for symbol in symbols
               if symbol not in rows or not rows[symbol].last_success]
    if missing:
        return {"active": True, "message": "Monitoring enabled. First successful quote sync is pending for " + ", ".join(missing) + "."}
    oldest = min(rows[symbol].last_success for symbol in symbols)
    if oldest < timezone.now() - timedelta(days=8):
        return {"active": True, "message": "A quote source is stale; check Yahoo prices before relying on values."}
    return {"active": True, "message": "Yahoo sync working. Last confirmed refresh: " + timezone.localtime(oldest).strftime("%d %b %Y %H:%M") + "."}


def _next_quarter_settlement(today):
    """First future calendar quarter-end, after which a market review is due."""
    from calendar import monthrange
    from datetime import date
    m = ((today.month - 1) // 3 + 1) * 3
    d = date(today.year, m, monthrange(today.year, m)[1])
    if today > d:
        m += 3
        year = today.year
        if m > 12:
            m = 3
            year += 1
        d = date(year, m, monthrange(year, m)[1])
    return d


def _alert_settings_status(owner):
    pref = NotificationPreference.objects.filter(owner=owner).first()
    cfg = SystemIntegration.objects.filter(pk=1).first()
    email_ready = bool(pref and pref.email_enabled and pref.destination_email and
                       cfg and cfg.smtp_host and cfg.smtp_sender)
    discord_ready = bool(pref and pref.discord_enabled and cfg and
                         cfg.discord_url_ciphertext)
    return {
        "email": email_ready, "discord": discord_ready,
        "enabled": email_ready or discord_ready,
    }


def render_strategy(request, strategy, entry_form=None, price_form=None):
    report = ledger_summary(strategy)
    holdings = []
    for symbol, qty in sorted(report["positions"].items()):
        mark = report["marks"].get(symbol)
        holdings.append({"symbol": symbol, "quantity": qty, "mark": mark, "value": (qty * mark.price) if mark else None})
    entries = strategy.ledger.all().order_by("-occurred_on", "-pk")[:60]
    chart_points = [{"date": snap.as_of.isoformat(),
                     "value": float(snap.total_value),
                     "contributed": float(snap.net_contributed)}
                    for snap in strategy.value_snapshots.order_by("as_of")]
    from .instrument_mapping import for_strategy
    instrument_profile = for_strategy(strategy)
    tab = request.GET.get("tab", "overview")
    if entry_form is not None:
        # Invalid manual trades must remain visible, with their form errors.
        tab = "ledger"
    elif price_form is not None:
        tab = "setup"
    if tab not in ("overview", "signals", "ledger", "setup"):
        tab = "overview"
    withdrawals = strategy.ledger.filter(kind=LedgerEntry.WITHDRAW).exists()
    result_pct = (
        (report["profit"] / report["net_contributed"] * 100)
        if (report["profit"] is not None and report["net_contributed"] > 0 and not withdrawals)
        else None
    )
    equity_qty = report["positions"].get(strategy.equity_symbol, 0)
    equity_mark = report["marks"].get(strategy.equity_symbol)
    equity_weight = (
        (equity_qty * equity_mark.price / report["value"] * 100)
        if (equity_qty > 0 and equity_mark is not None and report["value"] is not None
            and report["value"] > 0 and not report["missing"]) else None
    )
    pending_signals = strategy.signals.filter(acknowledged_at__isnull=True).count()
    return render(request, "strategy.html", {
        "strategy": strategy, "report": report, "holdings": holdings,
        "active_tab": tab, "result_pct": result_pct, "equity_weight": equity_weight,
        "pending_signals": pending_signals,
        "instrument_profile": instrument_profile,
        "chart_points": chart_points,
        "monitoring_status": _monitoring_status(strategy),
        "alert_status": _alert_settings_status(request.user),
        "next_quarter_end": _next_quarter_settlement(timezone.localdate()),
        "entries": entries, "signals": strategy.signals.all()[:12],
        "entry_form": entry_form or LedgerForm(strategy=strategy), "price_form": price_form or PriceForm(),
        "automation_form": AutomationForm(instance=strategy),
    })

def _return_to_strategy_tab(request, pk, tab):
    """Preserve the active view after normal form submissions."""
    if request.GET.get("tab") == tab:
        return redirect(reverse("strategy_detail", kwargs={"pk": pk}) + "?tab=" + tab)
    return redirect("strategy_detail", pk=pk)


@login_required
def strategy_detail(request, pk):
    strategy = get_object_or_404(StrategyInstance.objects.select_related("account", "preset").exclude(account__kind="PAPER"), pk=pk, owner=request.user)
    return render_strategy(request, strategy)

@login_required
@require_POST
def record_entry(request, pk):
    strategy = get_object_or_404(StrategyInstance.objects.exclude(account__kind="PAPER"), pk=pk, owner=request.user)
    form = LedgerForm(request.POST, strategy=strategy)
    if form.is_valid():
        entry = form.save(commit=False)
        try:
            validate_and_record(strategy, entry)
            messages.success(request, "Transaction recorded. Values update from your actual ledger.")
            return _return_to_strategy_tab(request, pk, "ledger")
        except ValidationError as exc:
            form.add_error(None, exc)
    return render_strategy(request, strategy, entry_form=form)


@login_required
@require_POST
def save_automation(request, pk):
    strategy = get_object_or_404(StrategyInstance.objects.select_related("preset").exclude(account__kind="PAPER"), pk=pk, owner=request.user)
    form = AutomationForm(request.POST, instance=strategy)
    if form.is_valid():
        form.save()
        messages.success(request, "Monitoring settings saved. Check Yahoo prices to verify your tickers.")
    else:
        messages.error(request, "Invalid settings. Use two distinct exact Yahoo tickers and enable only on 9Sig.")
    return _return_to_strategy_tab(request, pk, "setup")


@login_required
@require_POST
def refresh_market(request, pk):
    strategy = get_object_or_404(StrategyInstance.objects.select_related("preset", "account").exclude(account__kind="PAPER"), pk=pk, owner=request.user)
    if not strategy.market_data_enabled:
        messages.warning(request, "Enable Yahoo monitoring and add exact tickers to the strategy first.")
    else:
        try:
            result = review_strategy(strategy)
            if result["status"] == "ok":
                messages.success(request, "Checked Yahoo daily prices and 9Sig review rules. Any new action appears below.")
            else:
                messages.warning(request, "Market update unavailable: " + result["status"])
        except Exception:
            messages.error(request, "Price check could not complete. No estimates or trading instructions were invented.")
    return _return_to_strategy_tab(request, pk, "signals")


@login_required
@require_POST
def confirm_strategy_reset(request, pk):
    """Retired: free-text reset acknowledgement is NEVER execution evidence.

    Kept as a safe bookmark for legacy screens. The Section G owner-confirmed
    guided workflow, with linked actual fills and verified later GBP closes, is
    the only route that may create a new quarterly reset baseline.
    Provisional SPIKE_WATCH notices remain warnings requiring independent
    reference and execution confirmation, never a synthetic 60/40 baseline.
    """
    get_object_or_404(StrategyInstance.objects.exclude(account__kind="PAPER"), pk=pk, owner=request.user)
    messages.error(request,
        "This old reset shortcut cannot anchor a strategy. Review the active "
        "quarterly instruction and record actual fills separately. A verified "
        "post-fill portfolio is required before any reset.")
    return _return_to_strategy_tab(request, pk, "signals")


@login_required
@require_POST
def review_signal(request, pk, signal_pk):
    """Review advisory guidance without modifying broker holdings or signal math."""
    strategy = get_object_or_404(StrategyInstance.objects.exclude(account__kind="PAPER"), pk=pk, owner=request.user)
    signal = get_object_or_404(StrategySignal, pk=signal_pk, strategy=strategy)
    outcome = request.POST.get("outcome", "")
    note = request.POST.get("review_note", "").strip()[:2000]
    if outcome not in ("CONFIRMED", "FALSE_POSITIVE", "NOT_APPLICABLE"):
        messages.error(request, "Choose a valid review outcome.")
        return _return_to_strategy_tab(request, pk, "signals")
    if outcome == "FALSE_POSITIVE" and not note:
        messages.error(request, "Add a short correction note when reporting a false 9Sig signal.")
        return _return_to_strategy_tab(request, pk, "signals")
    with transaction.atomic():
        record = StrategySignal.objects.select_for_update().get(pk=signal.pk)
        record.review_outcome = outcome
        record.review_note = note
        record.acknowledged_at = timezone.now()
        record.save(update_fields=["review_outcome", "review_note", "acknowledged_at"])
    messages.success(request, "Strategy notice reviewed. No holdings, cash or broker orders were changed.")
    return _return_to_strategy_tab(request, pk, "signals")


@login_required
@require_POST
def acknowledge_signal(request, pk, signal_pk):
    strategy = get_object_or_404(StrategyInstance.objects.exclude(account__kind="PAPER"), pk=pk, owner=request.user)
    signal = get_object_or_404(StrategySignal, pk=signal_pk, strategy=strategy)
    if signal.acknowledged_at is None:
        signal.acknowledged_at = timezone.now()
        signal.save(update_fields=["acknowledged_at"])
    return _return_to_strategy_tab(request, pk, "signals")


@login_required
@require_POST
def record_price(request, pk):
    strategy = get_object_or_404(StrategyInstance.objects.exclude(account__kind="PAPER"), pk=pk, owner=request.user)
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
                    "price": form.cleaned_data["price"], "as_of": form.cleaned_data["as_of"], "source": "manual",
                })
                messages.success(request, "Price saved. Record a wealth snapshot on the overview to add a chart point.")
                return _return_to_strategy_tab(request, pk, "setup")
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
