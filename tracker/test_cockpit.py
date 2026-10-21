from datetime import date, timedelta
from uuid import uuid4
from decimal import Decimal as D
from io import BytesIO
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import (Account, LedgerEntry, MarketObservation, PriceMark,
                     RebalanceExecution, StrategyBaseline, StrategyInstance,
                     StrategyPreset, StrategySignal, BrokerImportRow)
from .performance import (xirr, time_weighted_return, max_drawdown, simple_return,
                          annualized_return, cumulative_from_annualized)
from .services import ledger_summary, validate_and_record
from .management.commands.sync_market_and_signals import save_verified_strategy_snapshot
from .cockpit_notifications import send_due_cockpit_notices
from .market_signals import quarter_end_before
from .core.catalogue import CATALOGUE_VERSION, onboarding_manifest, released_version

class PerformanceSafetyTests(TestCase):
    def test_no_data_and_undefined_cases(self):
        self.assertIsNone(xirr([], date(2026, 12, 31), D('100')))
        self.assertIsNone(time_weighted_return([]))
        self.assertIsNone(max_drawdown([]))
        self.assertIsNone(simple_return(D('100'), D('0')))
        self.assertIsNone(annualized_return(D('0.1'), date(2026,1,1), date(2026,1,1)))
    def test_single_deposit_and_annual_growth(self):
        rate = xirr([(date(2025,1,1), D('-1000'))], date(2026,1,1), D('1100'))
        self.assertIsNotNone(rate)
        self.assertAlmostEqual(rate, D('0.10'), delta=D('0.001'))
        self.assertEqual(simple_return(D('1100'), D('1000')), D('0.1'))
    def test_deposit_and_withdrawal(self):
        rate = xirr([(date(2025,1,1), D('-1000')),
                     (date(2025,7,1), D('-200')),
                     (date(2025,10,1), D('100'))], date(2026,1,1), D('1300'))
        self.assertIsNotNone(rate)
        self.assertGreater(rate, D('0'))
        self.assertIsNone(simple_return(D('1300'), D('1200'), D('100')))
    def test_twr_segments_and_drawdown(self):
        self.assertEqual(time_weighted_return([(D('100'), D('110')),
            (D('200'), D('210'))]), D('0.155'))
        self.assertEqual(max_drawdown([D('100'), D('120'), D('90'), D('135')]), D('0.25'))
    def test_multiple_irr_roots_declined(self):
        self.assertIsNone(xirr([(date(2025,1,1), D('-100')),
            (date(2025,6,1), D('300')),
            (date(2025,12,1), D('-250'))], date(2026,1,1), D('20')))

class GuidedRebalanceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('cockpit-owner', password='strong-password')
        self.client.force_login(self.user)
        self.account = Account.objects.create(owner=self.user, name='My ISA', kind='ISA', broker='TRADING212')
        preset, _ = StrategyPreset.objects.get_or_create(slug='nasdaq-quarterly-target', defaults={
            'name':'9Sig', 'summary':'', 'risk':'HIGH', 'methodology':'', 'active':True})
        self.strategy = StrategyInstance.objects.create(owner=self.user, account=self.account,
            preset=preset, name='9Sig', monthly_target=D('500'),
            equity_symbol='LQQ3.L', safe_symbol='ERNS.L', market_data_enabled=True)
        self.today = timezone.localdate()
        self.qend = quarter_end_before(self.today)
        self.fill_day = self.qend + timedelta(days=1)
        self.mark_day = self.today - timedelta(days=1)
        # Tests use current date; if run at very beginning of quarter, Yahoo
        # post-fill close must be later. Mock only the local clock in that case.
        if self.fill_day > self.mark_day:
            self.mark_day = self.today
        self.start = self.qend - timedelta(days=10)
        validate_and_record(self.strategy, LedgerEntry(kind='DEPOSIT', amount=D('1000'), occurred_on=self.start))
        validate_and_record(self.strategy, LedgerEntry(kind='BUY', symbol='LQQ3.L',
            quantity=D('7'), unit_price=D('100'), occurred_on=self.start))
        validate_and_record(self.strategy, LedgerEntry(kind='BUY', symbol='ERNS.L',
            quantity=D('6'), unit_price=D('50'), occurred_on=self.start))
        for symbol, price in [('LQQ3.L', D('100')), ('ERNS.L', D('50'))]:
            for day in (self.qend, self.mark_day):
                MarketObservation.objects.update_or_create(symbol=symbol, as_of=day,
                    defaults={'currency':'GBP', 'price':price})
            PriceMark.objects.create(strategy=self.strategy, symbol=symbol,
                                     price=price, as_of=self.mark_day, source='yahoo')
        self.sig = StrategySignal.objects.create(strategy=self.strategy,
            signal_key='quarter:test:'+str(self.qend), kind='QUARTERLY', as_of=self.qend,
            title='Quarterly reset', message='Time to review a reset to 60% equity / 40% safe',
            reset_reason='Explicit strategy reset',
            actions=[{'side':'SELL', 'symbol':'LQQ3.L', 'quantity':'1', 'estimated_gbp':'100.00'},
                     {'side':'BUY', 'symbol':'ERNS.L', 'quantity':'2', 'estimated_gbp':'100.00'}])
    def single_payload(self, index, *, quantity, price, fee='0'):
        return {
            'intent': 'record_one', 'action_index': str(index),
            'executed_on': self.fill_day.isoformat(),
            'quantity': str(quantity), 'unit_price': str(price),
            'fee': str(fee), 'idempotency_key': str(uuid4()), 'actual_confirmation': 'yes',
        }

    def record_both_actual_fills(self):
        uri = reverse('v2_guided_review', args=[self.strategy.pk, self.sig.pk])
        a = self.client.post(uri, self.single_payload(0, quantity='1', price='100'))
        b = self.client.post(uri, self.single_payload(1, quantity='2', price='50'))
        self.assertEqual(a.status_code, 302)
        self.assertEqual(b.status_code, 302)

    def test_atomic_fills_and_reset_baseline(self):
        self.record_both_actual_fills()
        self.assertFalse(RebalanceExecution.objects.filter(signal=self.sig).exists())
        response = self.client.post(reverse('v2_guided_review', args=[self.strategy.pk, self.sig.pk]),
                                    {'intent': 'finalize'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(RebalanceExecution.objects.filter(signal=self.sig).count(), 1)
        self.assertEqual(StrategyBaseline.objects.filter(reset_signal=self.sig).count(), 1)
        self.assertEqual(ledger_summary(self.strategy)['positions']['LQQ3.L'], D('6'))

    def test_overselling_rolls_back_all_rows_and_baseline(self):
        previous = self.strategy.ledger.count()
        self.client.post(reverse('v2_guided_review', args=[self.strategy.pk, self.sig.pk]),
                         self.single_payload(0, quantity='100', price='100'))
        self.assertEqual(self.strategy.ledger.count(), previous)
        self.assertFalse(RebalanceExecution.objects.filter(signal=self.sig).exists())
        self.assertFalse(StrategyBaseline.objects.filter(reset_signal=self.sig).exists())

    def test_historical_quarter_url_does_not_write_any_fills(self):
        self.sig.as_of = self.qend - timedelta(days=1)
        self.sig.save(update_fields=['as_of'])
        previous = self.strategy.ledger.count()
        self.assertEqual(self.client.get(reverse('v2_guided_review', args=[self.strategy.pk, self.sig.pk])).status_code, 200)
        self.assertEqual(self.client.post(reverse('v2_guided_review', args=[self.strategy.pk, self.sig.pk]), self.single_payload(0, quantity='1', price='100')).status_code, 302)
        self.assertEqual(self.strategy.ledger.count(), previous)
        self.assertFalse(RebalanceExecution.objects.filter(signal=self.sig).exists())

    def test_mismatched_market_dates_do_not_create_snapshot(self):
        safe_mark = PriceMark.objects.get(strategy=self.strategy, symbol='ERNS.L')
        safe_mark.as_of = self.mark_day - timedelta(days=1)
        safe_mark.save(update_fields=['as_of'])
        MarketObservation.objects.update_or_create(symbol='ERNS.L', as_of=safe_mark.as_of,
            defaults={'currency': 'GBP', 'price': safe_mark.price})
        self.assertIsNone(save_verified_strategy_snapshot(self.strategy))
        self.assertEqual(self.strategy.value_snapshots.count(), 0)

    def test_unverified_quote_currency_does_not_create_snapshot(self):
        MarketObservation.objects.filter(symbol='ERNS.L', as_of=self.mark_day).update(currency='USD')
        self.assertIsNone(save_verified_strategy_snapshot(self.strategy))
        self.assertEqual(self.strategy.value_snapshots.count(), 0)

    def test_unmapped_broker_cannot_confirm_quarterly_fills(self):
        self.account.broker = 'UNKNOWN'
        self.account.save(update_fields=['broker'])
        count = self.strategy.ledger.count()
        self.client.post(reverse('v2_guided_review', args=[self.strategy.pk, self.sig.pk]),
                         self.single_payload(0, quantity='1', price='100'))
        self.assertEqual(self.strategy.ledger.count(), count)
        self.assertFalse(RebalanceExecution.objects.filter(signal=self.sig).exists())

    def test_invalidated_signal_never_emits_quarterly_reminder(self):
        self.strategy.monthly_target = D('0')
        self.strategy.save(update_fields=['monthly_target'])
        StrategySignal.objects.filter(pk=self.sig.pk).update(
            created_at=timezone.now() - timedelta(days=7))
        validate_and_record(self.strategy, LedgerEntry(
            kind='DEPOSIT', occurred_on=self.today, amount=D('100')))
        with patch('tracker.cockpit_notifications.dispatch') as delivery:
            send_due_cockpit_notices(today=self.today)
        delivery.assert_not_called()

    def test_owner_isolation(self):
        stranger = get_user_model().objects.create_user('stranger', password='strong-password')
        self.client.force_login(stranger)
        self.assertEqual(self.client.get(reverse('v2_guided_review', args=[self.strategy.pk, self.sig.pk])).status_code, 404)


class NonResetQuarterTests(GuidedRebalanceTests):
    def test_ordinary_safe_asset_fill_remains_partial_until_all_real_units(self):
        self.sig.reset_reason = ''
        self.sig.save(update_fields=['reset_reason'])
        url = reverse('v2_guided_review', args=[self.strategy.pk, self.sig.pk])
        self.client.post(url, self.single_payload(0, quantity='1', price='100'))
        self.client.post(url, self.single_payload(1, quantity='0.5', price='50'))
        self.client.post(url, {'intent': 'finalize'})
        self.assertFalse(RebalanceExecution.objects.filter(signal=self.sig).exists())
        self.assertFalse(StrategyBaseline.objects.filter(reset_signal=self.sig).exists())

    def test_ordinary_trade_does_not_reanchor(self):
        self.sig.reset_reason = ''
        self.sig.save(update_fields=['reset_reason'])
        self.record_both_actual_fills()
        response = self.client.post(
            reverse('v2_guided_review', args=[self.strategy.pk, self.sig.pk]),
            {'intent': 'finalize'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(RebalanceExecution.objects.filter(signal=self.sig).count(), 1)
        self.assertFalse(StrategyBaseline.objects.filter(reset_signal=self.sig).exists())

class CashOnlyTodayTests(TestCase):
    def test_cash_only_initial_portfolio_shows_actual_cash(self):
        user = get_user_model().objects.create_user('cash-only-owner', password='strong-password')
        account = Account.objects.create(owner=user, name='Trading 212 ISA', kind='ISA', broker='TRADING212')
        preset = StrategyPreset.objects.get(slug='nasdaq-quarterly-target')
        strategy = StrategyInstance.objects.create(owner=user, account=account, preset=preset,
            name='9Sig', equity_symbol='LQQ3.L', safe_symbol='ERNS.L', market_data_enabled=True)
        validate_and_record(strategy, LedgerEntry(kind='DEPOSIT', occurred_on=timezone.localdate(), amount=D('1000')))
        self.client.force_login(user)
        response = self.client.get(reverse('v2_instance_page', args=[strategy.pk, 'today']))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['today_cash'], D('1000.00'))
        self.assertEqual(strategy.ledger.filter(kind='BUY').count(), 0)


