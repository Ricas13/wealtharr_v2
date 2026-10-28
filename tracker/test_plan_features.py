"""The first features wired to plans: strategy limit, alert channels and the Study what-if."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tenancy import access, features
from tenancy.models import Plan, UserAccess
from tracker.models import NotificationPreference, StrategyInstance
from tracker.notification_routing import selected_channels
from tracker.test_tenancy_core import _strategy

PASSWORD = "plan-features-password-1"


class PlanFeatureBase(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user("member", password=PASSWORD, email="m@example.com")
        self.admin = User.objects.create_superuser("boss", password=PASSWORD)
        self.strategy = _strategy(self.user, "First")
        self.client.force_login(self.user)

    def put_on(self, plan, user=None):
        UserAccess.objects.update_or_create(user=user or self.user, defaults={"plan": plan})

    def basic(self, **kwargs):
        return Plan.objects.create(slug="basic", name="Basic", features=kwargs.pop("features", ["alerts.email"]),
                                   limits=kwargs.pop("limits", {"strategies.max": 1}), **kwargs)


class CatalogueTests(PlanFeatureBase):
    def test_the_features_are_declared_and_the_full_plan_includes_all_of_them(self):
        keys = {"strategies.max", "alerts.email", "alerts.discord", "alerts.telegram", "study.simulate"}
        self.assertTrue(keys <= {f.key for f in features.all_features()})
        self.assertTrue(features.get("strategies.max").limit)
        for key in keys - {"strategies.max"}:
            self.assertTrue(access.can(self.user, key), key)
        self.assertIsNone(access.limit(self.user, "strategies.max"))


class AlertChannelTests(PlanFeatureBase):
    def _pref(self):
        pref, _ = NotificationPreference.objects.get_or_create(owner=self.user)
        pref.email_enabled = pref.discord_enabled = pref.telegram_enabled = True
        pref.discord_user_id, pref.telegram_chat_id = "123", "456"
        pref.destination_email = "m@example.com"
        pref.save()
        return pref

    def test_sending_uses_only_channels_the_plan_includes(self):
        pref = self._pref()
        self.assertEqual(set(selected_channels(pref, "quarterly")), {"email", "discord", "telegram"})
        self.put_on(self.basic())
        self.assertEqual(selected_channels(pref, "quarterly"), ("email",))

    def test_settings_refuses_to_switch_on_a_channel_the_plan_lacks_and_explains(self):
        self.put_on(self.basic())
        pref = self._pref()
        pref.discord_enabled = False
        pref.save()
        page = self.client.get(reverse("v2_settings"))
        self.assertContains(page, "does not include alerts by Discord, Telegram")
        response = self.client.post(reverse("v2_settings"), {
            "form_kind": "owner", "destination_email": "m@example.com", "email_enabled": "on",
            "discord_enabled": "on"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your plan does not include this alert channel.")
        pref.refresh_from_db()
        self.assertFalse(pref.discord_enabled)
        ok = self.client.post(reverse("v2_settings"), {
            "form_kind": "owner", "destination_email": "m@example.com", "email_enabled": "on"})
        self.assertEqual(ok.status_code, 302)

    def test_the_owner_is_not_limited_by_a_narrow_plan(self):
        self.put_on(self.basic(), self.admin)
        pref, _ = NotificationPreference.objects.get_or_create(owner=self.admin)
        pref.discord_enabled = True
        pref.save()
        self.assertIn("discord", selected_channels(pref, "quarterly"))


class StudyWhatIfTests(PlanFeatureBase):
    def test_the_what_if_endpoint_needs_the_feature(self):
        url = reverse("v2_study_simulate_json", args=[self.strategy.pk])
        self.assertNotEqual(self.client.get(url).status_code, 403, "Full includes it")
        self.put_on(self.basic())
        self.assertEqual(self.client.get(url).status_code, 403)
        self.put_on(Plan.objects.create(slug="study", name="Study", features=["study.simulate"]))
        self.assertNotEqual(self.client.get(url).status_code, 403)


class StrategyLimitTests(PlanFeatureBase):
    def _second(self, name="Second"):
        day = (timezone.localdate() - timedelta(days=2)).isoformat()
        return self.client.post(reverse("v2_add_strategy"), {
            "strategy": "nasdaq-quarterly-target", "platform": "TRADING212", "account_type": "ISA",
            "ack": "yes", "ack_choice": "yes", "mode": "fresh", "name": name, "start": day,
            "amount": "500", "deposited": "yes"})

    def test_a_plan_with_a_limit_stops_the_next_strategy_with_a_clear_reason(self):
        self.put_on(self.basic(limits={"strategies.max": 1}))
        page = self.client.get(reverse("v2_add_strategy"))
        self.assertContains(page, "strategy limit is reached")
        response = self._second()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your plan allows 1 strategy")
        self.assertEqual(StrategyInstance.objects.filter(owner=self.user).count(), 1)

    def test_a_bigger_limit_and_the_full_plan_allow_more(self):
        self.put_on(self.basic(limits={"strategies.max": 2}))
        self.assertNotContains(self.client.get(reverse("v2_add_strategy")), "strategy limit is reached")
        response = self._second()
        self.assertEqual(response.status_code, 302, getattr(response, "context", None) and
                         response.context.get("wizard_error"))
        self.assertEqual(StrategyInstance.objects.filter(owner=self.user).count(), 2)
        self.assertContains(self.client.get(reverse("v2_add_strategy")), "strategy limit is reached")
