"""Section B: immutable exact-line mapping and evidence never masquerading as ISA access."""
from dataclasses import FrozenInstanceError
from decimal import Decimal
from unittest import TestCase

from tracker.instrument_mapping import (
    CURATED_ISA_ROLE_MAP, EQUITY_UK, SAFE_UK, InstrumentMappingUnavailable,
    MAPPING_VERSION, RESEARCH_VERIFIED_ON, resolve_curated_isa_role,
    resolve_instruments,
)


class ReviewedInstrumentCatalogueTests(TestCase):
    def test_only_exact_isa_role_keys_are_curated(self):
        self.assertEqual(len(CURATED_ISA_ROLE_MAP), 2)
        growth = resolve_curated_isa_role(
            "nasdaq-quarterly-target", "growth", "GB", "TRADING212", "ISA")
        safe = resolve_curated_isa_role(
            "nasdaq-quarterly-target", "safe", "GB", "TRADING212", "ISA")
        self.assertIs(growth, EQUITY_UK)
        self.assertIs(safe, SAFE_UK)
        self.assertEqual(growth.role, "growth")
        self.assertEqual(safe.role, "safe")
        self.assertEqual(growth.verified_on, RESEARCH_VERIFIED_ON)
        self.assertEqual(safe.verified_on, RESEARCH_VERIFIED_ON)

    def test_unsupported_account_version_or_role_cannot_substitute(self):
        for role, account, version in (
            ("growth", "GIA", MAPPING_VERSION),
            ("safe", "SIPP", MAPPING_VERSION),
            ("safe", "ISA", "unreleased"),
            ("hedge", "ISA", MAPPING_VERSION),
        ):
            with self.subTest(role=role, account=account, version=version):
                with self.assertRaises(InstrumentMappingUnavailable):
                    resolve_curated_isa_role(
                        "nasdaq-quarterly-target", role, "GB",
                        "TRADING212", account, version)

    def test_exact_identity_and_no_double_gbx_scaling(self):
        self.assertEqual(EQUITY_UK.isin, "IE00BLRPRL42")
        self.assertEqual(EQUITY_UK.yahoo_ticker, "LQQ3.L")
        self.assertEqual(EQUITY_UK.broker_ticker, "LQQ3")
        self.assertEqual(EQUITY_UK.currency, "GBP")
        self.assertEqual(EQUITY_UK.raw_quote_unit, "GBX")
        self.assertEqual(EQUITY_UK.raw_to_gbp_factor, Decimal("0.01"))
        self.assertEqual(Decimal("30773.99") * EQUITY_UK.raw_to_gbp_factor,
                         Decimal("307.7399"))
        self.assertEqual(SAFE_UK.isin, "IE00BCRY6441")
        self.assertEqual(SAFE_UK.yahoo_ticker, "ERNS.L")
        self.assertEqual(SAFE_UK.currency, "GBP")
        self.assertEqual(SAFE_UK.raw_quote_unit, "GBP")
        self.assertEqual(SAFE_UK.raw_to_gbp_factor, Decimal("1"))

    def test_sources_are_evidence_not_account_permission(self):
        for instrument in (EQUITY_UK, SAFE_UK):
            with self.subTest(instrument=instrument.broker_ticker):
                self.assertTrue(instrument.issuer_says_isa_eligible)
                self.assertTrue(instrument.broker_invest_listed)
                self.assertFalse(instrument.broker_isa_confirmed)
                self.assertFalse(instrument.owner_product_permission_confirmed)
                self.assertFalse(instrument.yahoo_price_unit_confirmed)
                self.assertTrue(instrument.isa_access_requires_owner_check)
                self.assertTrue(instrument.issuer_isa_evidence_url.startswith("https://"))
                self.assertIn("/invest/", instrument.broker_evidence_url)

    def test_catalogue_and_released_instruments_are_immutable(self):
        with self.assertRaises(TypeError):
            CURATED_ISA_ROLE_MAP["other"] = EQUITY_UK
        with self.assertRaises(FrozenInstanceError):
            EQUITY_UK.isin = "some-other-id"

    def test_legacy_profile_remains_compatible_for_existing_code(self):
        # Existing GIA runtime/tests stay untouched until section F migration.
        profile = resolve_instruments(
            "nasdaq-quarterly-target", "GB", "ISA", "TRADING212")
        self.assertEqual(profile.mapping_version, MAPPING_VERSION)
        self.assertIs(profile.equity, EQUITY_UK)
        self.assertIs(profile.safe, SAFE_UK)
