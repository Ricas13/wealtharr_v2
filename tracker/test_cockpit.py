from datetime import date, timedelta
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
from .cockpit_import import inspect_csv
from .market_signals import quarter_end_before

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
            actions=[{'side':'SELL', 'symbol':'LQQ3.L', 'quantity':'1', 'estimated_gbp':'100.00'},
                     {'side':'BUY', 'symbol':'ERNS.L', 'quantity':'2', 'estimated_gbp':'100.00'}])
    def payload(self, qty='1', price='100'):
        return {'executed_on': self.fill_day.isoformat(),
                'fill0-quantity':qty, 'fill0-unit_price':price, 'fill0-fee':'0',
                'fill1-quantity':'2', 'fill1-unit_price':'50', 'fill1-fee':'0'}
    def test_atomic_fills_and_reset_baseline(self):
        # If today is only one day after quarter-end, post-fill closing mark
        # must exist; fixture ensures this by using today for quote date.
        response = self.client.post(reverse('cockpit_rebalance', args=[self.sig.pk]), self.payload())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(RebalanceExecution.objects.filter(signal=self.sig).count(), 1)
        self.assertEqual(StrategyBaseline.objects.filter(reset_signal=self.sig).count(), 1)
        self.assertEqual(ledger_summary(self.strategy)['positions']['LQQ3.L'], D('6'))
        self.client.post(reverse('cockpit_rebalance', args=[self.sig.pk]), self.payload())
        self.assertEqual(RebalanceExecution.objects.filter(signal=self.sig).count(), 1)
    def test_overselling_rolls_back_all_rows_and_baseline(self):
        before = LedgerEntry.objects.filter(strategy=self.strategy).count()
        self.client.post(reverse('cockpit_rebalance', args=[self.sig.pk]), self.payload(qty='100'))
        self.assertEqual(LedgerEntry.objects.filter(strategy=self.strategy).count(), before)
        self.assertFalse(RebalanceExecution.objects.filter(signal=self.sig).exists())
        self.assertFalse(StrategyBaseline.objects.filter(reset_signal=self.sig).exists())
    def test_owner_isolation(self):
        stranger = get_user_model().objects.create_user('stranger', password='strong-password')
        self.client.force_login(stranger)
        self.assertEqual(self.client.get(reverse('cockpit_rebalance', args=[self.sig.pk])).status_code, 404)

class ImportSafetyTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('import-owner',password='strong-password')
        account = Account.objects.create(owner=self.user, name='ISA', kind='ISA', broker='TRADING212')
        preset, _ = StrategyPreset.objects.get_or_create(slug='nasdaq-quarterly-target', defaults={
            'name':'9Sig', 'summary':'', 'risk':'HIGH', 'methodology':'', 'active':True})
        self.strategy = StrategyInstance.objects.create(owner=self.user, account=account,preset=preset,
            name='9Sig', equity_symbol='LQQ3.L', safe_symbol='ERNS.L')
    def file(self, text):
        return SimpleUploadedFile('history.csv', text.encode('utf-8'), content_type='text/csv')
    def test_preview_duplicate_in_same_file(self):
        text = 'Action,Time,Total (GBP),ID\nDeposit,2026-01-02 12:00:00,500,dep-1\nDeposit,2026-01-02 12:00:00,500,dep-1\n'
        entries, errors, duplicate_lines = inspect_csv(self.file(text), self.strategy)
        self.assertEqual(len(entries), 1)
        self.assertEqual(errors, [])
        self.assertEqual(duplicate_lines, [3])
        self.assertEqual(self.strategy.ledger.count(), 0)
    def test_unknown_currency_fails_closed(self):
        text = ('Action,Time,Ticker,No. of shares,Price / share,Currency (Price / share),ID\n'
                'Market buy,2026-01-02 12:00:00,LQQ3,1,10,USD,order-1\n')
        entries, errors, duplicate_lines = inspect_csv(self.file(text), self.strategy)
        self.assertFalse(entries)
        self.assertEqual(len(errors), 1)
    def test_import_endpoint_never_partial_on_error(self):
        self.client.force_login(self.user)
        csv = ('Action,Time,Total (GBP),ID\nDeposit,2026-01-02 12:00:00,500,d1\n'
               'Currency conversion,2026-01-02 12:01:00,10,bad\n')
        self.client.post(reverse('cockpit_csv'), {'csv_file': self.file(csv), 'apply':'yes'})
        self.assertEqual(self.strategy.ledger.count(), 0)
        self.assertEqual(BrokerImportRow.objects.count(), 0)

class CSVImportCommitTests(ImportSafetyTests):
    def test_commit_and_repeat_is_idempotent(self):
        self.client.force_login(self.user)
        csv_data = ('Action,Time,Total (GBP),Ticker,No. of shares,Price / share,'
                    'Currency (Price / share),ISIN,ID\n'
                    'Deposit,2026-01-02 12:00:00,1000,,,,,,d1\n'
                    'Market buy,2026-01-03 12:00:00,500,LQQ3,5,100,GBP,IE00BLRPRL42,o1\n')
        first = self.client.post(reverse('cockpit_csv'),
            {'csv_file': self.file(csv_data), 'apply': 'yes'})
        self.assertEqual(first.status_code, 302)
        self.assertEqual(self.strategy.ledger.count(), 2)
        self.assertEqual(BrokerImportRow.objects.count(), 2)
        self.assertEqual(ledger_summary(self.strategy)['cash'], D('500.00'))
        self.client.post(reverse('cockpit_csv'),
            {'csv_file': self.file(csv_data), 'apply': 'yes'})
        self.assertEqual(self.strategy.ledger.count(), 2)

class NonResetQuarterTests(GuidedRebalanceTests):
    def test_ordinary_trade_does_not_reanchor(self):
        self.sig.message = 'Equity above quarterly 9Sig signal line; move towards target.'
        self.sig.save(update_fields=['message'])
        response = self.client.post(reverse('cockpit_rebalance', args=[self.sig.pk]), self.payload())
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
        response = self.client.get(reverse('cockpit_today'))
        self.assertContains(response, '1,000.00')
        self.assertContains(response, 'Add your actual purchases')
