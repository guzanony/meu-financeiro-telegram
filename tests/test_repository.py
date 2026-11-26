from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from mei_finance_bot.database import FinanceRepository


class FinanceRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.repository = FinanceRepository(Path(self.directory.name) / "finance.db")
        self.user_id = 123
        self.repository.ensure_user(self.user_id, "Dona Maria", 8_100_000)

    def tearDown(self) -> None:
        self.repository.close()
        self.directory.cleanup()

    def test_sale_receipt_updates_cash(self) -> None:
        machine_id = self.repository.create_machine(self.user_id, "Maquina A", 200, 250, 50, 0)
        sale = self.repository.create_sale(
            self.user_id,
            occurred_on=date(2026, 11, 25),
            gross_cents=10_000,
            payment_method="credito",
            machine_id=machine_id,
            installments=2,
            expected_on=date(2026, 12, 1),
            client_id=None,
            product_id=None,
            item_description="Servico",
            quantity=1,
            discount_cents=0,
            notes=None,
        )
        self.assertEqual(sale["fee_cents"], 300)
        self.assertEqual(sale["net_cents"], 9700)
        self.assertEqual(sale["status"], "a_receber")
        self.assertEqual(self.repository.dashboard(self.user_id)["receivable_cents"], 9700)
        self.repository.set_sale_status(self.user_id, int(sale["id"]), "recebida", date(2026, 12, 1))
        self.assertEqual(self.repository.dashboard(self.user_id)["total_balance_cents"], 9700)

    def test_recurring_payable_creates_next_bill(self) -> None:
        bill = self.repository.create_bill(
            self.user_id,
            kind="pagar",
            description="Aluguel",
            amount_cents=2_000,
            due_on=date(2026, 11, 30),
            category="aluguel",
            barcode=None,
            recurrence_months=1,
        )
        self.repository.mark_bill_paid(self.user_id, int(bill["id"]), paid_on=date(2026, 11, 30))
        bills = self.repository.list_bills(self.user_id)
        self.assertEqual(len(bills), 2)
        self.assertEqual(bills[0]["status"], "pago")
        self.assertEqual(bills[1]["due_on"], "2026-12-30")

    def test_anticipation_applies_machine_fee(self) -> None:
        machine_id = self.repository.create_machine(self.user_id, "Maquina B", 0, 0, 0, 500)
        sale = self.repository.create_sale(
            self.user_id,
            occurred_on=date(2026, 11, 25),
            gross_cents=10_000,
            payment_method="credito",
            machine_id=machine_id,
            installments=1,
            expected_on=date(2026, 12, 25),
            client_id=None,
            product_id=None,
            item_description="Servico parcelado",
            quantity=1,
            discount_cents=0,
            notes=None,
        )
        anticipated, fee_cents = self.repository.anticipate_sale(self.user_id, int(sale["id"]), date(2026, 11, 25))
        self.assertEqual(fee_cents, 500)
        self.assertEqual(anticipated["net_cents"], 9500)
        self.assertEqual(self.repository.dashboard(self.user_id)["total_balance_cents"], 9500)
