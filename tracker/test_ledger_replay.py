"""One ledger fold, two entry points: validating replay and trusted read-back."""
from datetime import date, timedelta
from decimal import Decimal as D
from types import SimpleNamespace as Row

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from tracker import services
from tracker.v2_ledger_core import replay, replay_trusted

DAY = date(2026, 1, 5)


def row(pk, kind, *, day=DAY, symbol="", quantity="0", price="0", amount="0", fee="0"):
    return Row(pk=pk, kind=kind, occurred_on=day, symbol=symbol, quantity=D(quantity),
               unit_price=D(price), amount=D(amount), fee=D(fee), deleted=False)


LEDGER = [
    row(1, "DEPOSIT", amount="1000"),
    row(2, "BUY", day=DAY + timedelta(days=1), symbol="A", quantity="6", price="100", fee="1.50"),
    row(3, "DIVIDEND", day=DAY + timedelta(days=2), amount="5"),
    row(4, "SELL", day=DAY + timedelta(days=3), symbol="A", quantity="2", price="110", fee="1"),
    row(5, "WITHDRAW", day=DAY + timedelta(days=4), amount="100"),
    row(6, "SPLIT", day=DAY + timedelta(days=5), symbol="A", quantity="2"),
    row(7, "FEE", day=DAY + timedelta(days=6), amount="2"),
    row(8, "INTEREST", day=DAY + timedelta(days=7), amount="0.5"),
]


class OneFoldTests(SimpleTestCase):
    def test_both_entry_points_agree_on_a_valid_ledger(self):
        strict = replay(LEDGER)
        cash, funded, positions = replay_trusted(LEDGER)
        self.assertEqual((strict["cash"], strict["net_contributed"], strict["positions"]),
                         (cash.quantize(D("0.01")), funded.quantize(D("0.01")), positions))
        # 1000 - 600 - 1.5 + 5 + 220 - 1 - 100 - 2 + 0.5 = 521.00; 4 shares doubled to 8.
        self.assertEqual(strict["cash"], D("521.00"))
        self.assertEqual(strict["positions"], {"A": D("8.00000000")})
        self.assertEqual(strict["net_contributed"], D("900.00"))

    def test_the_services_wrapper_keeps_its_old_shape(self):
        cash, funded, positions = services.replay(LEDGER)
        self.assertEqual((cash, funded, positions), replay_trusted(LEDGER))

    def test_validating_replay_rejects_what_trusted_read_back_accepts(self):
        short = [row(1, "DEPOSIT", amount="100"),
                 row(2, "BUY", day=DAY + timedelta(days=1), symbol="A", quantity="5", price="100")]
        with self.assertRaises(ValidationError):
            replay(short)
        self.assertEqual(replay(short, strict=False)["cash"], D("-400.00"))
        self.assertEqual(replay_trusted(short)[0], D("-400.00"))

    def test_malformed_rows_are_only_rejected_by_the_validating_replay(self):
        odd = [row(1, "DEPOSIT", amount="100"), row(2, "SPLIT", day=DAY + timedelta(days=1),
                                                    symbol="A", quantity="2")]
        with self.assertRaises(ValidationError):
            replay(odd)  # a split needs shares held
        self.assertEqual(replay_trusted(odd)[2], {})
        with self.assertRaises(ValidationError):
            replay([row(1, "DEPOSIT", day=date.today() + timedelta(days=3), amount="1")])

    def test_deleted_rows_are_skipped_and_order_follows_date_then_pk(self):
        gone = row(9, "DEPOSIT", amount="999")
        gone.deleted = True
        shuffled = [LEDGER[3], LEDGER[1], gone, LEDGER[0], LEDGER[2], LEDGER[4], LEDGER[5], LEDGER[6], LEDGER[7]]
        self.assertEqual(replay(shuffled), replay(LEDGER))
