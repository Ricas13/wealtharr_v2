"""Fixes from the outside review: alerts, labels, overview honesty."""
from datetime import timedelta
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tracker import v2_dashboard as dash
from tracker.models import (
    Account, InviteRequest, NotificationDelivery, StrategyInstance, StrategyPreset,
)


def make_instance(username, slug="all-weather", name="Strategy"):
    user = get_user_model().objects.create_user(username, password="test-strong-1")
    account = Account.objects.create(owner=user, name=name + " ISA", kind="ISA", broker="TRADING212")
    preset, _ = StrategyPreset.objects.get_or_create(
        slug=slug, defaults={"name": slug, "summary": "", "risk": "LOW"})
    return StrategyInstance.objects.create(
        owner=user, account=account, preset=preset, name=name, strategy_key=slug,
        tracking_only=False, market_data_enabled=True)


class InviteRetryTests(TestCase):
    def post(self):
        with patch("tracker.v2_home._turnstile_verified", return_value=True):
            return self.client.post(reverse("request_invite"), {"email": "a@example.com"})

    def test_an_unnotified_invite_is_retried_until_an_alert_goes_out(self):
        with patch("tracker.v2_home._notify_owners") as notify:
            self.post()
            self.post()
        self.assertEqual(notify.call_count, 2)
        InviteRequest.objects.update(notified=True)
        with patch("tracker.v2_home._notify_owners") as notify:
            self.post()
        notify.assert_not_called()


class LastSentTests(TestCase):
    def test_last_sent_ignores_failed_and_pending_deliveries(self):
        strategy = make_instance("last-sent")
        base = dict(owner=strategy.owner, strategy=strategy, channel="email", category="quarterly")
        sent = NotificationDelivery.objects.create(event_key="a", status="SENT", **base)
        NotificationDelivery.objects.create(event_key="b", status="FAILED", **base)
        NotificationDelivery.objects.create(event_key="c", status="PENDING", **base)
        summary = dash.alerts_summary(strategy.owner)
        self.assertEqual(summary["last"].event_key, "c")
        self.assertEqual(summary["last_sent"].pk, sent.pk)


class NotificationIsolationTests(TestCase):
    def test_one_failing_strategy_does_not_stop_the_others(self):
        from tracker import cockpit_notifications as notes
        make_instance("iso-a", name="A")
        make_instance("iso-b", name="B")
        seen = []

        def fake(strategy, today, remind):
            seen.append(strategy.name)
            if strategy.name == seen[0]:
                raise ArithmeticError("corrupt ledger")
            return 1

        with patch.object(notes, "_notify_strategy", side_effect=fake):
            with self.assertLogs(notes.LOG, level="ERROR"):
                count = notes.send_due_cockpit_notices(timezone.localdate())
        self.assertEqual(sorted(seen), ["A", "B"])
        self.assertEqual(count, 1)

    def test_subjects_use_the_strategys_own_cadence(self):
        from tracker.cockpit_notifications import _cadence
        self.assertEqual(_cadence(make_instance("cad-a", "all-weather")), "yearly")
        self.assertEqual(_cadence(make_instance("cad-b", "nasdaq-trend-dca")), "daily")
        self.assertEqual(_cadence(make_instance("cad-c", "nine-sig-quarterly")), "quarterly")


class CombinedChartTests(TestCase):
    def test_strategies_without_certified_history_are_named_not_hidden(self):
        a, b = make_instance("chart-a", name="Alpha"), make_instance("chart-b", name="Beta")
        day1, day2 = timezone.localdate() - timedelta(days=2), timezone.localdate() - timedelta(days=1)
        series = {a.pk: [(day1, D("100"), D("90")), (day2, D("110"), D("90"))], b.pk: []}
        with patch.object(dash, "certified_points", side_effect=lambda s, report=None: series[s.pk]):
            points, omitted = dash.combine_with_omitted([a, b])
        self.assertEqual(omitted, ["Beta"])
        self.assertEqual([p[0] for p in points], [day1, day2])
        with patch.object(dash, "certified_points", return_value=[]):
            self.assertEqual(dash.combine_with_omitted([a, b]), ([], ["Alpha", "Beta"]))


class ReturnLabelTests(TestCase):
    def test_history_does_not_call_the_daily_chain_a_time_weighted_return(self):
        from django.template.loader import get_template
        source = get_template("v2/history.html").template.source
        self.assertNotIn("time-weighted", source.lower())
        self.assertIn("approximate", source)


class OverviewSupersededTests(TestCase):
    def test_overview_does_not_show_quantities_the_ledger_has_overtaken(self):
        from tracker.models import LedgerEntry, StrategySignal
        from tracker.v2_history import _open_action
        strategy = make_instance("ov-sup", "all-weather")
        today = timezone.localdate()
        # all-weather is yearly; use the plug-in's own last review date.
        from tracker.strategies.registry import last_review_before
        as_of = last_review_before(strategy, today)
        StrategySignal.objects.create(
            strategy=strategy, signal_key="ov:%s" % as_of, kind="QUARTERLY", as_of=as_of,
            title="t", message="m", actions=[{
                "side": "BUY", "symbol": "VUAG.L", "quantity": "1", "estimated_gbp": "100"}])
        summary = {"positions": {"VUAG.L": D("1")}, "cash": D("0")}
        with patch("tracker.v2_go_live.setup_missing", return_value=False):
            action = _open_action(strategy, summary, today)
            self.assertEqual(action["kind"], "review")
            LedgerEntry.objects.create(
                strategy=strategy, kind=LedgerEntry.WITHDRAW, amount=D("10"),
                occurred_on=as_of + timedelta(days=1))
            action = _open_action(strategy, summary, today)
        self.assertEqual((action["kind"], action["title"]), ("data", "Review out of date"))


class IsaAndAllocationHonestyTests(TestCase):
    def test_isa_allowance_counts_only_pound_isa_accounts(self):
        usd = make_instance("isa-usd")
        Account.objects.filter(pk=usd.account_id).update(currency="USD")
        usd.account.refresh_from_db()
        with patch.object(dash, "effective_entries", create=True, return_value=[]):
            self.assertIsNone(dash.isa_allowance([usd]))


class ClientAddressTests(TestCase):
    def request(self, forwarded="", remote="10.0.0.1", **extra):
        from django.test import RequestFactory
        meta = {"REMOTE_ADDR": remote}
        if forwarded:
            meta["HTTP_X_FORWARDED_FOR"] = forwarded
        return RequestFactory().post("/", extra, **meta)

    def test_no_trusted_hops_means_no_per_visitor_identity(self):
        from tracker.client_ip import client_ip
        with self.settings(TRUSTED_PROXY_HOPS=0):
            self.assertEqual(client_ip(self.request("1.2.3.4")), "")

    def test_the_address_is_counted_from_the_trusted_end(self):
        from tracker.client_ip import client_ip
        with self.settings(TRUSTED_PROXY_HOPS=2):
            # A visitor may prepend anything; the last two entries are the proxies'.
            self.assertEqual(client_ip(self.request("6.6.6.6, 1.2.3.4, 172.16.0.1")), "1.2.3.4")
            self.assertEqual(client_ip(self.request("172.16.0.1")), "")  # chain too short

    def test_a_stranger_cannot_lock_the_owner_out_from_another_address(self):
        from tracker.v2_auth import LOGIN_FAILURE_LIMIT
        make_instance("owner-lock")
        with self.settings(TRUSTED_PROXY_HOPS=1), \
                patch("tracker.v2_auth._turnstile_verified", return_value=True):
            for _ in range(LOGIN_FAILURE_LIMIT):
                self.client.post(reverse("login"), {"username": "owner-lock", "password": "x"},
                                 HTTP_X_FORWARDED_FOR="9.9.9.9")
            blocked = self.client.post(reverse("login"), {"username": "owner-lock", "password": "x"},
                                       HTTP_X_FORWARDED_FOR="9.9.9.9")
            self.assertContains(blocked, "Too many failed sign-ins")
            ok = self.client.post(reverse("login"), {"username": "owner-lock", "password": "test-strong-1"},
                                  HTTP_X_FORWARDED_FOR="8.8.8.8")
            self.assertEqual(ok.status_code, 302)

    def test_without_trusted_hops_the_username_bucket_still_applies(self):
        from tracker.v2_auth import LOGIN_FAILURE_LIMIT
        make_instance("owner-lock2")
        with self.settings(TRUSTED_PROXY_HOPS=0), \
                patch("tracker.v2_auth._turnstile_verified", return_value=True):
            for _ in range(LOGIN_FAILURE_LIMIT):
                self.client.post(reverse("login"), {"username": "owner-lock2", "password": "x"})
            blocked = self.client.post(reverse("login"), {"username": "owner-lock2", "password": "test-strong-1"})
            self.assertContains(blocked, "Too many failed sign-ins")

    def test_one_visitor_cannot_use_up_the_shared_comparison_budget(self):
        from tracker import v2_home
        for _ in range(v2_home.PUBLIC_FRESH_PER_VISITOR):
            v2_home._public_budget("1.1.1.1")
        with self.assertRaises(v2_home.Busy):
            v2_home._public_budget("1.1.1.1")
        v2_home._public_budget("2.2.2.2")  # another visitor still has budget


class DeliveryRecordFailureTests(TestCase):
    def test_a_failure_saving_the_result_does_not_abort_dispatch(self):
        from tracker.models import NotificationPreference
        from tracker.services import dispatch
        strategy = make_instance("dispatch-fail")
        NotificationPreference.objects.update_or_create(
            owner=strategy.owner, defaults={"email_enabled": True, "destination_email": "a@example.com"})
        real_save = NotificationDelivery.save

        def flaky(self, *args, **kwargs):
            if kwargs.get("update_fields") == ["status", "error", "next_retry_at"]:
                raise RuntimeError("connection dropped")
            return real_save(self, *args, **kwargs)

        with patch("tracker.services._send_message"), \
                patch.object(NotificationDelivery, "save", flaky), \
                self.assertLogs("tracker.services", level="ERROR"):
            outcomes = dispatch(strategy.owner, "k1", "s", "m", strategy=strategy)
        self.assertEqual(outcomes["email"], "pending")


class ColourTests(TestCase):
    def test_colour_does_not_fail_without_a_preset(self):
        from tracker.strategies.registry import DEFAULT_COLOUR, colour_for
        self.assertEqual(colour_for(SimpleNamespace(strategy_key="", preset=None)), DEFAULT_COLOUR)
