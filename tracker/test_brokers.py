"""Broker-neutral catalogue: brokers come from evidence on each fund listing."""
from dataclasses import replace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from tracker.core import catalogue
from tracker.core.brokers import BROKERS
from tracker.core.catalogue import ALL_WEATHER, STRATEGY_9SIG, rows_for, selection_available


class BrokerNeutralCatalogueTests(TestCase):
    def test_a_broker_without_evidence_is_offered_nothing(self):
        self.assertIn("AJBELL", BROKERS)
        for key in (STRATEGY_9SIG, ALL_WEATHER):
            self.assertEqual(rows_for(key, "AJBELL", "ISA"), ())
            self.assertFalse(selection_available(key, "AJBELL", "ISA"))
        self.assertFalse(selection_available(STRATEGY_9SIG, "NO-SUCH-BROKER", "ISA"))
        self.assertEqual({key: mapped for key, _, mapped in catalogue.platforms()}["AJBELL"], False)
        self.assertEqual({key: mapped for key, _, mapped in catalogue.platforms()}["TRADING212"], True)

    def test_evidence_on_a_listing_offers_that_broker_the_same_exact_line(self):
        roles = {}
        for (key, role, country), listings in catalogue.ROLE_LISTINGS.items():
            first = listings[0]
            brokers = {**first.brokers, "AJBELL": "https://example.invalid/%s" % first.isin}
            roles[(key, role, country)] = (replace(first, brokers=brokers),) + listings[1:]
        with patch.dict(catalogue.ROLE_LISTINGS, roles):
            rows = catalogue._build()
        with patch.dict(catalogue._CATALOGUE, rows, clear=True):
            for key in (STRATEGY_9SIG, ALL_WEATHER):
                aj = rows_for(key, "AJBELL", "ISA")
                t212 = rows_for(key, "TRADING212", "ISA")
                self.assertEqual({(r.role, r.isin, r.ticker) for r in aj},
                                 {(r.role, r.isin, r.ticker) for r in t212})
                self.assertTrue(all(r.broker_name == "AJ Bell" for r in aj))
                self.assertTrue(selection_available(key, "AJBELL", "ISA"))
                manifest = catalogue.onboarding_manifest(key, "AJBELL", "ISA")
                self.assertEqual(manifest["broker"], "AJBELL")
            # AJ Bell's SIPP has no wrapper evidence on these listings.
            self.assertFalse(selection_available(ALL_WEATHER, "AJBELL", "SIPP"))

    def test_preferred_listing_wins_and_fallback_is_used_only_with_evidence(self):
        key = (ALL_WEATHER, "gold", "GB")
        primary = catalogue.ROLE_LISTINGS[key][0]
        other = replace(primary, isin="XS0000000001", ticker="ALT.L", brokers={"HL": "https://example.invalid"})
        with patch.dict(catalogue.ROLE_LISTINGS, {key: (primary, other)}):
            rows = catalogue._build()
        self.assertEqual(rows[(ALL_WEATHER, "gold", "GB", "TRADING212", "ISA", catalogue.CATALOGUE_VERSION)].ticker,
                         primary.ticker)
        self.assertEqual(rows[(ALL_WEATHER, "gold", "GB", "HL", "ISA", catalogue.CATALOGUE_VERSION)].ticker, "ALT.L")


class SetupBrokerTests(TestCase):
    def setUp(self):
        owner = get_user_model().objects.create_user("broker-owner", password="a-long-test-password-1")
        self.client.force_login(owner)

    def test_setup_lists_brokers_and_names_the_chosen_one(self):
        page = self.client.get(reverse("v2_add_strategy"))
        self.assertContains(page, "AJ Bell")
        self.assertContains(page, "in my own Trading 212 account")
        unmapped = self.client.get(reverse("v2_add_strategy") + "?platform=AJBELL")
        self.assertContains(unmapped, "No reviewed funds for this strategy at this broker yet")
