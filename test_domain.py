from datetime import date
import unittest

from mei_finance_bot.domain import ValidationError, add_months, normalize_barcode, parse_cents, split_cents


class DomainTest(unittest.TestCase):
    def test_parses_brazilian_currency(self) -> None:
        self.assertEqual(parse_cents("R$ 1.234,56"), 123456)
        self.assertEqual(parse_cents("1234.56"), 123456)

    def test_validates_barcode_length(self) -> None:
        self.assertEqual(normalize_barcode("1" * 47), "1" * 47)
        with self.assertRaises(ValidationError):
            normalize_barcode("123")

    def test_splits_amount_and_months(self) -> None:
        self.assertEqual(split_cents(100, 3), [34, 33, 33])
        self.assertEqual(add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
