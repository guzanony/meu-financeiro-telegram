from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterator

from .domain import (
    add_months,
    brl,
    iso,
    normalize_barcode,
    percentage,
    split_cents,
    today,
)


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
  telegram_id INTEGER PRIMARY KEY,
  full_name TEXT NOT NULL,
  accountant_email TEXT,
  annual_limit_cents INTEGER NOT NULL DEFAULT 8100000,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  initial_balance_cents INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS machines (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  debit_fee_bps INTEGER NOT NULL,
  credit_fee_bps INTEGER NOT NULL,
  installment_fee_bps INTEGER NOT NULL DEFAULT 0,
  anticipation_fee_bps INTEGER NOT NULL DEFAULT 0,
  contracted_fee_bps INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS clients (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  document TEXT,
  phone TEXT,
  email TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  price_cents INTEGER NOT NULL DEFAULT 0,
  sku TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sales (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
  occurred_on TEXT NOT NULL,
  gross_cents INTEGER NOT NULL,
  discount_cents INTEGER NOT NULL DEFAULT 0,
  fee_cents INTEGER NOT NULL DEFAULT 0,
  fee_bps INTEGER NOT NULL DEFAULT 0,
  net_cents INTEGER NOT NULL,
  payment_method TEXT NOT NULL,
  installments INTEGER NOT NULL DEFAULT 1,
  expected_on TEXT NOT NULL,
  status TEXT NOT NULL,
  machine_id INTEGER REFERENCES machines(id),
  client_id INTEGER REFERENCES clients(id),
  product_id INTEGER REFERENCES products(id),
  item_description TEXT,
  quantity INTEGER NOT NULL DEFAULT 1,
  notes TEXT,
  fiscal_document TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sale_installments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
  number INTEGER NOT NULL,
  expected_on TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  received_on TEXT,
  status TEXT NOT NULL DEFAULT 'a_receber'
);

CREATE TABLE IF NOT EXISTS bills (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  description TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  due_on TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'aberto',
  category TEXT,
  barcode TEXT,
  recurrence_months INTEGER,
  paid_on TEXT,
  interest_cents INTEGER NOT NULL DEFAULT 0,
  account_id INTEGER REFERENCES accounts(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS transactions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
  account_id INTEGER REFERENCES accounts(id),
  kind TEXT NOT NULL,
  description TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  occurred_on TEXT NOT NULL,
  category TEXT,
  source_type TEXT,
  source_id INTEGER,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS attachments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
  entity_type TEXT NOT NULL,
  entity_id INTEGER NOT NULL,
  path TEXT NOT NULL,
  telegram_file_id TEXT NOT NULL,
  mime_type TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS alert_config (
  user_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
  alert_type TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  days_before INTEGER NOT NULL DEFAULT 3,
  PRIMARY KEY (user_id, alert_type)
);

CREATE TABLE IF NOT EXISTS alert_deliveries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
  alert_key TEXT NOT NULL,
  delivered_on TEXT NOT NULL,
  UNIQUE(user_id, alert_key, delivered_on)
);

CREATE TABLE IF NOT EXISTS report_schedules (
  user_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
  period TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  send_to_telegram INTEGER NOT NULL DEFAULT 1,
  send_to_accountant INTEGER NOT NULL DEFAULT 0,
  last_sent_on TEXT,
  PRIMARY KEY (user_id, period)
);
"""

DEFAULT_ALERTS = {
    "boleto": 7,
    "imposto": 7,
    "limite": 0,
    "recebimento": 3,
    "caixa": 0,
    "taxa": 0,
}


class FinanceRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(database_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.executescript(SCHEMA)
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Cursor]:
        cursor = self._connection.cursor()
        try:
            yield cursor
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        finally:
            cursor.close()

    def _one(self, query: str, parameters: tuple[Any, ...]) -> sqlite3.Row | None:
        return self._connection.execute(query, parameters).fetchone()

    def _all(self, query: str, parameters: tuple[Any, ...]) -> list[sqlite3.Row]:
        return self._connection.execute(query, parameters).fetchall()

    def ensure_user(self, telegram_id: int, full_name: str, annual_limit_cents: int) -> None:
        with self._transaction() as cursor:
            cursor.execute(
                "INSERT OR IGNORE INTO users (telegram_id, full_name, annual_limit_cents) VALUES (?, ?, ?)",
                (telegram_id, full_name or "Usuario", annual_limit_cents),
            )
            cursor.execute("UPDATE users SET full_name = ? WHERE telegram_id = ?", (full_name or "Usuario", telegram_id))
            cursor.execute(
                "INSERT OR IGNORE INTO accounts (user_id, name, kind) VALUES (?, 'Caixa', 'caixa')",
                (telegram_id,),
            )
            for alert_type, days_before in DEFAULT_ALERTS.items():
                cursor.execute(
                    "INSERT OR IGNORE INTO alert_config (user_id, alert_type, days_before) VALUES (?, ?, ?)",
                    (telegram_id, alert_type, days_before),
                )

    def user(self, user_id: int) -> sqlite3.Row:
        row = self._one("SELECT * FROM users WHERE telegram_id = ?", (user_id,))
        if not row:
            raise ValueError("Usuario nao inicializado.")
        return row

    def all_user_ids(self) -> list[int]:
        return [row["telegram_id"] for row in self._all("SELECT telegram_id FROM users", ())]

    def default_account_id(self, user_id: int) -> int:
        row = self._one(
            "SELECT id FROM accounts WHERE user_id = ? AND active = 1 ORDER BY id LIMIT 1", (user_id,)
        )
        if not row:
            raise ValueError("Cadastre uma conta antes de registrar um movimento.")
        return int(row["id"])

    def create_account(self, user_id: int, name: str, kind: str, initial_balance_cents: int) -> int:
        with self._transaction() as cursor:
            cursor.execute(
                "INSERT INTO accounts (user_id, name, kind, initial_balance_cents) VALUES (?, ?, ?, ?)",
                (user_id, name.strip(), kind.strip().lower(), initial_balance_cents),
            )
            return int(cursor.lastrowid)

    def list_accounts(self, user_id: int) -> list[sqlite3.Row]:
        return self._all(
            """
            SELECT a.*, a.initial_balance_cents + COALESCE(SUM(
              CASE WHEN t.kind = 'entrada' THEN t.amount_cents ELSE -t.amount_cents END
            ), 0) AS balance_cents
            FROM accounts a LEFT JOIN transactions t ON t.account_id = a.id
            WHERE a.user_id = ? AND a.active = 1
            GROUP BY a.id ORDER BY a.id
            """,
            (user_id,),
        )

    def create_machine(
        self,
        user_id: int,
        name: str,
        debit_fee_bps: int,
        credit_fee_bps: int,
        installment_fee_bps: int,
        anticipation_fee_bps: int,
    ) -> int:
        with self._transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO machines
                (user_id, name, debit_fee_bps, credit_fee_bps, installment_fee_bps, anticipation_fee_bps, contracted_fee_bps)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    name.strip(),
                    debit_fee_bps,
                    credit_fee_bps,
                    installment_fee_bps,
                    anticipation_fee_bps,
                    credit_fee_bps,
                ),
            )
            return int(cursor.lastrowid)

    def list_machines(self, user_id: int) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM machines WHERE user_id = ? AND active = 1 ORDER BY name", (user_id,))

    def machine(self, user_id: int, machine_id: int) -> sqlite3.Row:
        row = self._one("SELECT * FROM machines WHERE id = ? AND user_id = ?", (machine_id, user_id))
        if not row:
            raise ValueError("Maquininha nao encontrada.")
        return row

    def create_client(self, user_id: int, name: str, document: str | None, phone: str | None, email: str | None) -> int:
        with self._transaction() as cursor:
            cursor.execute(
                "INSERT INTO clients (user_id, name, document, phone, email) VALUES (?, ?, ?, ?, ?)",
                (user_id, name.strip(), document, phone, email),
            )
            return int(cursor.lastrowid)

    def list_clients(self, user_id: int) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM clients WHERE user_id = ? ORDER BY name", (user_id,))

    def create_product(self, user_id: int, name: str, price_cents: int, sku: str | None) -> int:
        with self._transaction() as cursor:
            cursor.execute(
                "INSERT INTO products (user_id, name, price_cents, sku) VALUES (?, ?, ?, ?)",
                (user_id, name.strip(), price_cents, sku),
            )
            return int(cursor.lastrowid)

    def list_products(self, user_id: int) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM products WHERE user_id = ? ORDER BY name", (user_id,))

    def _belongs_to_user(self, table: str, user_id: int, record_id: int) -> bool:
        allowed = {"clients", "products", "machines", "accounts", "sales", "bills"}
        if table not in allowed:
            return False
        return self._one(f"SELECT 1 FROM {table} WHERE id = ? AND user_id = ?", (record_id, user_id)) is not None

    def create_sale(
        self,
        user_id: int,
        *,
        occurred_on: date,
        gross_cents: int,
        payment_method: str,
        machine_id: int | None,
        installments: int,
        expected_on: date,
        client_id: int | None,
        product_id: int | None,
        item_description: str | None,
        quantity: int,
        discount_cents: int,
        notes: str | None,
    ) -> sqlite3.Row:
        if discount_cents > gross_cents:
            raise ValueError("O desconto nao pode ser maior que a venda.")
        if client_id and not self._belongs_to_user("clients", user_id, client_id):
            raise ValueError("Cliente nao encontrado.")
        if product_id and not self._belongs_to_user("products", user_id, product_id):
            raise ValueError("Produto nao encontrado.")
        fee_bps = 0
        if machine_id:
            machine = self.machine(user_id, machine_id)
            fee_bps = int(machine["debit_fee_bps"] if payment_method == "debito" else machine["credit_fee_bps"])
            if payment_method == "credito" and installments > 1:
                fee_bps += int(machine["installment_fee_bps"]) * (installments - 1)
        taxable_cents = gross_cents - discount_cents
        fee_cents = taxable_cents * fee_bps // 10_000
        net_cents = taxable_cents - fee_cents
        immediate = payment_method in {"pix", "dinheiro", "debito"}
        status = "recebida" if immediate else "a_receber"
        with self._transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO sales
                (user_id, occurred_on, gross_cents, discount_cents, fee_cents, fee_bps, net_cents,
                 payment_method, installments, expected_on, status, machine_id, client_id, product_id,
                 item_description, quantity, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    iso(occurred_on),
                    gross_cents,
                    discount_cents,
                    fee_cents,
                    fee_bps,
                    net_cents,
                    payment_method,
                    installments,
                    iso(expected_on),
                    status,
                    machine_id,
                    client_id,
                    product_id,
                    item_description,
                    quantity,
                    notes,
                ),
            )
            sale_id = int(cursor.lastrowid)
            paid_parts = split_cents(net_cents, installments)
            for number, amount in enumerate(paid_parts, start=1):
                installment_date = add_months(expected_on, number - 1)
                cursor.execute(
                    """
                    INSERT INTO sale_installments (sale_id, number, expected_on, amount_cents, received_on, status)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sale_id,
                        number,
                        iso(installment_date),
                        amount,
                        iso(occurred_on) if immediate else None,
                        "recebida" if immediate else "a_receber",
                    ),
                )
            if immediate:
                cursor.execute(
                    """
                    INSERT INTO transactions (user_id, account_id, kind, description, amount_cents, occurred_on, category, source_type, source_id)
                    VALUES (?, ?, 'entrada', ?, ?, ?, 'venda', 'sale', ?)
                    """,
                    (user_id, self.default_account_id(user_id), f"Venda #{sale_id}", net_cents, iso(occurred_on), sale_id),
                )
        return self.sale(user_id, sale_id)

    def sale(self, user_id: int, sale_id: int) -> sqlite3.Row:
        row = self._one(
            """
            SELECT s.*, c.name AS client_name, p.name AS product_name, m.name AS machine_name
            FROM sales s
            LEFT JOIN clients c ON c.id = s.client_id
            LEFT JOIN products p ON p.id = s.product_id
            LEFT JOIN machines m ON m.id = s.machine_id
            WHERE s.user_id = ? AND s.id = ?
            """,
            (user_id, sale_id),
        )
        if not row:
            raise ValueError("Venda nao encontrada.")
        return row

    def list_sales(self, user_id: int, start: date | None = None, end: date | None = None) -> list[sqlite3.Row]:
        clauses = ["s.user_id = ?"]
        parameters: list[Any] = [user_id]
        if start:
            clauses.append("s.occurred_on >= ?")
            parameters.append(iso(start))
        if end:
            clauses.append("s.occurred_on <= ?")
            parameters.append(iso(end))
        return self._all(
            f"""
            SELECT s.*, c.name AS client_name, p.name AS product_name, m.name AS machine_name
            FROM sales s
            LEFT JOIN clients c ON c.id = s.client_id
            LEFT JOIN products p ON p.id = s.product_id
            LEFT JOIN machines m ON m.id = s.machine_id
            WHERE {' AND '.join(clauses)} ORDER BY s.occurred_on DESC, s.id DESC
            """,
            tuple(parameters),
        )

    def sale_installments(self, user_id: int, sale_id: int) -> list[sqlite3.Row]:
        self.sale(user_id, sale_id)
        return self._all("SELECT * FROM sale_installments WHERE sale_id = ? ORDER BY number", (sale_id,))

    def set_sale_status(self, user_id: int, sale_id: int, status: str, occurred_on: date | None = None) -> sqlite3.Row:
        if status not in {"recebida", "cancelada", "estornada"}:
            raise ValueError("Status de venda invalido.")
        sale = self.sale(user_id, sale_id)
        reference_date = occurred_on or today()
        if sale["status"] in {"cancelada", "estornada"}:
            raise ValueError("Esta venda ja foi cancelada ou estornada.")
        with self._transaction() as cursor:
            if status == "recebida":
                cursor.execute("UPDATE sales SET status = 'recebida' WHERE id = ?", (sale_id,))
                cursor.execute(
                    "UPDATE sale_installments SET status = 'recebida', received_on = ? WHERE sale_id = ? AND status = 'a_receber'",
                    (iso(reference_date), sale_id),
                )
                existing = cursor.execute(
                    "SELECT id FROM transactions WHERE user_id = ? AND source_type = 'sale' AND source_id = ?",
                    (user_id, sale_id),
                ).fetchone()
                if not existing:
                    cursor.execute(
                        """
                        INSERT INTO transactions (user_id, account_id, kind, description, amount_cents, occurred_on, category, source_type, source_id)
                        VALUES (?, ?, 'entrada', ?, ?, ?, 'venda', 'sale', ?)
                        """,
                        (user_id, self.default_account_id(user_id), f"Venda #{sale_id}", sale["net_cents"], iso(reference_date), sale_id),
                    )
            elif status == "cancelada":
                if sale["status"] == "recebida":
                    raise ValueError("Use /estornar para uma venda ja recebida.")
                cursor.execute("UPDATE sales SET status = 'cancelada' WHERE id = ?", (sale_id,))
                cursor.execute("UPDATE sale_installments SET status = 'cancelada' WHERE sale_id = ?", (sale_id,))
            else:
                if sale["status"] != "recebida":
                    raise ValueError("Apenas vendas recebidas podem ser estornadas.")
                cursor.execute("UPDATE sales SET status = 'estornada' WHERE id = ?", (sale_id,))
                cursor.execute("UPDATE sale_installments SET status = 'estornada' WHERE sale_id = ?", (sale_id,))
                cursor.execute(
                    """
                    INSERT INTO transactions (user_id, account_id, kind, description, amount_cents, occurred_on, category, source_type, source_id)
                    VALUES (?, ?, 'saida', ?, ?, ?, 'estorno', 'sale_refund', ?)
                    """,
                    (user_id, self.default_account_id(user_id), f"Estorno venda #{sale_id}", sale["net_cents"], iso(reference_date), sale_id),
                )
        return self.sale(user_id, sale_id)

    def anticipate_sale(self, user_id: int, sale_id: int, occurred_on: date | None = None) -> tuple[sqlite3.Row, int]:
        sale = self.sale(user_id, sale_id)
        if sale["status"] != "a_receber":
            raise ValueError("Somente vendas a receber podem ser antecipadas.")
        if not sale["machine_id"]:
            raise ValueError("A antecipacao exige uma maquininha cadastrada na venda.")
        machine = self.machine(user_id, int(sale["machine_id"]))
        advance_fee_bps = int(machine["anticipation_fee_bps"])
        if not advance_fee_bps:
            raise ValueError("Cadastre uma taxa de antecipacao para esta maquininha.")
        outstanding = self._one(
            "SELECT COALESCE(SUM(amount_cents), 0) AS total FROM sale_installments WHERE sale_id = ? AND status = 'a_receber'",
            (sale_id,),
        )
        outstanding_cents = int(outstanding["total"])
        advance_fee_cents = outstanding_cents * advance_fee_bps // 10_000
        received_cents = outstanding_cents - advance_fee_cents
        reference_date = occurred_on or today()
        gross_after_discount = int(sale["gross_cents"]) - int(sale["discount_cents"])
        updated_fee_cents = int(sale["fee_cents"]) + advance_fee_cents
        updated_net_cents = int(sale["net_cents"]) - advance_fee_cents
        updated_fee_bps = updated_fee_cents * 10_000 // gross_after_discount if gross_after_discount else 0
        with self._transaction() as cursor:
            cursor.execute(
                "UPDATE sales SET status = 'recebida', fee_cents = ?, fee_bps = ?, net_cents = ? WHERE id = ?",
                (updated_fee_cents, updated_fee_bps, updated_net_cents, sale_id),
            )
            cursor.execute(
                "UPDATE sale_installments SET status = 'recebida', received_on = ? WHERE sale_id = ? AND status = 'a_receber'",
                (iso(reference_date), sale_id),
            )
            cursor.execute(
                """
                INSERT INTO transactions (user_id, account_id, kind, description, amount_cents, occurred_on, category, source_type, source_id)
                VALUES (?, ?, 'entrada', ?, ?, ?, 'antecipacao', 'sale', ?)
                """,
                (user_id, self.default_account_id(user_id), f"Antecipacao venda #{sale_id}", received_cents, iso(reference_date), sale_id),
            )
        return self.sale(user_id, sale_id), advance_fee_cents

    def update_sale_field(self, user_id: int, sale_id: int, field: str, value: str | None) -> sqlite3.Row:
        allowed_fields = {"descricao": "item_description", "observacoes": "notes", "nota": "fiscal_document"}
        column = allowed_fields.get(field.lower())
        if not column:
            raise ValueError("Campo editavel: descricao, observacoes ou nota.")
        self.sale(user_id, sale_id)
        with self._transaction() as cursor:
            cursor.execute(f"UPDATE sales SET {column} = ? WHERE id = ? AND user_id = ?", (value, sale_id, user_id))
        return self.sale(user_id, sale_id)

    def create_bill(
        self,
        user_id: int,
        *,
        kind: str,
        description: str,
        amount_cents: int,
        due_on: date,
        category: str | None,
        barcode: str | None,
        recurrence_months: int | None,
        account_id: int | None = None,
    ) -> sqlite3.Row:
        if kind not in {"pagar", "receber"}:
            raise ValueError("Tipo de boleto deve ser pagar ou receber.")
        if barcode:
            barcode = normalize_barcode(barcode)
        account_id = account_id or self.default_account_id(user_id)
        if not self._belongs_to_user("accounts", user_id, account_id):
            raise ValueError("Conta nao encontrada.")
        with self._transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO bills (user_id, kind, description, amount_cents, due_on, category, barcode, recurrence_months, account_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, kind, description.strip(), amount_cents, iso(due_on), category, barcode, recurrence_months, account_id),
            )
            bill_id = int(cursor.lastrowid)
        return self.bill(user_id, bill_id)

    def _refresh_bill_statuses(self, user_id: int) -> None:
        with self._transaction() as cursor:
            cursor.execute(
                "UPDATE bills SET status = 'vencido' WHERE user_id = ? AND status IN ('aberto', 'agendado') AND due_on < ?",
                (user_id, iso(today())),
            )

    def bill(self, user_id: int, bill_id: int) -> sqlite3.Row:
        self._refresh_bill_statuses(user_id)
        row = self._one("SELECT * FROM bills WHERE user_id = ? AND id = ?", (user_id, bill_id))
        if not row:
            raise ValueError("Boleto nao encontrado.")
        return row

    def list_bills(self, user_id: int, status: str | None = None) -> list[sqlite3.Row]:
        self._refresh_bill_statuses(user_id)
        query = "SELECT * FROM bills WHERE user_id = ?"
        parameters: tuple[Any, ...] = (user_id,)
        if status:
            query += " AND status = ?"
            parameters = (user_id, status)
        return self._all(query + " ORDER BY due_on, id", parameters)

    def mark_bill_paid(self, user_id: int, bill_id: int, interest_cents: int = 0, paid_on: date | None = None) -> sqlite3.Row:
        bill = self.bill(user_id, bill_id)
        if bill["status"] == "pago":
            raise ValueError("Este boleto ja esta pago.")
        paid_on = paid_on or today()
        total_cents = int(bill["amount_cents"]) + interest_cents
        transaction_kind = "saida" if bill["kind"] == "pagar" else "entrada"
        with self._transaction() as cursor:
            cursor.execute(
                "UPDATE bills SET status = 'pago', paid_on = ?, interest_cents = ? WHERE id = ?",
                (iso(paid_on), interest_cents, bill_id),
            )
            cursor.execute(
                """
                INSERT INTO transactions (user_id, account_id, kind, description, amount_cents, occurred_on, category, source_type, source_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'bill', ?)
                """,
                (
                    user_id,
                    bill["account_id"],
                    transaction_kind,
                    bill["description"],
                    total_cents,
                    iso(paid_on),
                    bill["category"],
                    bill_id,
                ),
            )
            if bill["recurrence_months"]:
                next_due = add_months(date.fromisoformat(bill["due_on"]), int(bill["recurrence_months"]))
                cursor.execute(
                    """
                    INSERT INTO bills (user_id, kind, description, amount_cents, due_on, category, barcode, recurrence_months, account_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        bill["kind"],
                        bill["description"],
                        bill["amount_cents"],
                        iso(next_due),
                        bill["category"],
                        bill["barcode"],
                        bill["recurrence_months"],
                        bill["account_id"],
                    ),
                )
        return self.bill(user_id, bill_id)

    def schedule_bill(self, user_id: int, bill_id: int) -> sqlite3.Row:
        bill = self.bill(user_id, bill_id)
        if bill["status"] not in {"aberto", "vencido"}:
            raise ValueError("Apenas boletos abertos ou vencidos podem ser agendados.")
        with self._transaction() as cursor:
            cursor.execute("UPDATE bills SET status = 'agendado' WHERE id = ?", (bill_id,))
        return self.bill(user_id, bill_id)

    def create_transaction(
        self,
        user_id: int,
        kind: str,
        description: str,
        amount_cents: int,
        occurred_on: date,
        category: str | None,
        account_id: int | None = None,
    ) -> int:
        if kind not in {"entrada", "saida"}:
            raise ValueError("Tipo de lancamento deve ser entrada ou saida.")
        account_id = account_id or self.default_account_id(user_id)
        with self._transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO transactions (user_id, account_id, kind, description, amount_cents, occurred_on, category)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, account_id, kind, description.strip(), amount_cents, iso(occurred_on), category),
            )
            return int(cursor.lastrowid)

    def add_attachment(
        self,
        user_id: int,
        entity_type: str,
        entity_id: int,
        path: str,
        telegram_file_id: str,
        mime_type: str | None,
    ) -> int:
        if entity_type not in {"sale", "bill"}:
            raise ValueError("Tipo de anexo invalido.")
        table = "sales" if entity_type == "sale" else "bills"
        if not self._belongs_to_user(table, user_id, entity_id):
            raise ValueError("Registro para anexo nao encontrado.")
        with self._transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO attachments (user_id, entity_type, entity_id, path, telegram_file_id, mime_type)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, entity_type, entity_id, path, telegram_file_id, mime_type),
            )
            return int(cursor.lastrowid)

    def set_annual_limit(self, user_id: int, amount_cents: int) -> None:
        with self._transaction() as cursor:
            cursor.execute("UPDATE users SET annual_limit_cents = ? WHERE telegram_id = ?", (amount_cents, user_id))

    def set_accountant_email(self, user_id: int, email: str | None) -> None:
        with self._transaction() as cursor:
            cursor.execute("UPDATE users SET accountant_email = ? WHERE telegram_id = ?", (email, user_id))

    def alert_settings(self, user_id: int) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM alert_config WHERE user_id = ? ORDER BY alert_type", (user_id,))

    def update_alert(self, user_id: int, alert_type: str, enabled: bool, days_before: int) -> None:
        if alert_type not in DEFAULT_ALERTS:
            raise ValueError("Tipo de alerta invalido.")
        with self._transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO alert_config (user_id, alert_type, enabled, days_before) VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, alert_type) DO UPDATE SET enabled = excluded.enabled, days_before = excluded.days_before
                """,
                (user_id, alert_type, int(enabled), days_before),
            )

    def update_report_schedule(self, user_id: int, period: str, destination: str, enabled: bool) -> None:
        if period not in {"diario", "semanal", "mensal", "anual"}:
            raise ValueError("Periodo deve ser diario, semanal, mensal ou anual.")
        if destination not in {"telegram", "contador"}:
            raise ValueError("Destino deve ser telegram ou contador.")
        existing = self._one(
            "SELECT * FROM report_schedules WHERE user_id = ? AND period = ?", (user_id, period)
        )
        telegram = int(existing["send_to_telegram"]) if existing else 0
        accountant = int(existing["send_to_accountant"]) if existing else 0
        if destination == "telegram":
            telegram = int(enabled)
        else:
            accountant = int(enabled)
        with self._transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO report_schedules (user_id, period, enabled, send_to_telegram, send_to_accountant)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, period) DO UPDATE SET
                  enabled = excluded.enabled,
                  send_to_telegram = excluded.send_to_telegram,
                  send_to_accountant = excluded.send_to_accountant
                """,
                (user_id, period, int(telegram or accountant), telegram, accountant),
            )

    def report_schedules(self, user_id: int) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM report_schedules WHERE user_id = ? ORDER BY period", (user_id,))

    def due_report_schedules(self, current: date | None = None) -> list[sqlite3.Row]:
        current = current or today()
        rows = self._all("SELECT * FROM report_schedules WHERE enabled = 1", ())
        due: list[sqlite3.Row] = []
        for row in rows:
            if row["last_sent_on"] == iso(current):
                continue
            period = row["period"]
            is_due = (
                period == "diario"
                or (period == "semanal" and current.weekday() == 0)
                or (period == "mensal" and current.day == 1)
                or (period == "anual" and current.month == 1 and current.day == 1)
            )
            if is_due:
                due.append(row)
        return due

    def mark_report_schedule_sent(self, user_id: int, period: str, sent_on: date | None = None) -> None:
        with self._transaction() as cursor:
            cursor.execute(
                "UPDATE report_schedules SET last_sent_on = ? WHERE user_id = ? AND period = ?",
                (iso(sent_on or today()), user_id, period),
            )

    def dashboard(self, user_id: int) -> dict[str, Any]:
        current = today()
        balances = self.list_accounts(user_id)
        total_balance = sum(int(row["balance_cents"]) for row in balances)
        receivable = self._one(
            """
            SELECT COALESCE(SUM(si.amount_cents), 0) AS total
            FROM sale_installments si JOIN sales s ON s.id = si.sale_id
            WHERE s.user_id = ? AND si.status = 'a_receber' AND s.status = 'a_receber'
            """,
            (user_id,),
        )["total"]
        forecasts: dict[int, int] = {}
        for days in (7, 15, 30):
            cutoff = iso(current + timedelta(days=days))
            inflow_sales = self._one(
                """
                SELECT COALESCE(SUM(si.amount_cents), 0) AS total FROM sale_installments si
                JOIN sales s ON s.id = si.sale_id
                WHERE s.user_id = ? AND si.status = 'a_receber' AND si.expected_on <= ?
                """,
                (user_id, cutoff),
            )["total"]
            bill_rows = self._all(
                """
                SELECT kind, COALESCE(SUM(amount_cents), 0) AS total FROM bills
                WHERE user_id = ? AND status IN ('aberto', 'agendado', 'vencido') AND due_on <= ? GROUP BY kind
                """,
                (user_id, cutoff),
            )
            bills = {row["kind"]: int(row["total"]) for row in bill_rows}
            forecasts[days] = total_balance + int(inflow_sales) + bills.get("receber", 0) - bills.get("pagar", 0)
        return {
            "accounts": balances,
            "total_balance_cents": total_balance,
            "receivable_cents": int(receivable),
            "forecasts": forecasts,
        }

    def annual_sales_total(self, user_id: int, year: int | None = None) -> int:
        year = year or today().year
        row = self._one(
            """
            SELECT COALESCE(SUM(gross_cents - discount_cents), 0) AS total FROM sales
            WHERE user_id = ? AND occurred_on >= ? AND occurred_on <= ? AND status NOT IN ('cancelada', 'estornada')
            """,
            (user_id, f"{year}-01-01", f"{year}-12-31"),
        )
        return int(row["total"])

    def report_data(self, user_id: int, start: date, end: date) -> dict[str, Any]:
        sales = self.list_sales(user_id, start, end)
        transactions = self._all(
            """
            SELECT * FROM transactions WHERE user_id = ? AND occurred_on BETWEEN ? AND ? ORDER BY occurred_on, id
            """,
            (user_id, iso(start), iso(end)),
        )
        gross = sum(int(row["gross_cents"]) - int(row["discount_cents"]) for row in sales if row["status"] not in {"cancelada", "estornada"})
        fees = sum(int(row["fee_cents"]) for row in sales if row["status"] not in {"cancelada", "estornada"})
        net = sum(int(row["net_cents"]) for row in sales if row["status"] not in {"cancelada", "estornada"})
        income = sum(int(row["amount_cents"]) for row in transactions if row["kind"] == "entrada")
        expenses = sum(int(row["amount_cents"]) for row in transactions if row["kind"] == "saida")
        payment_breakdown: dict[str, int] = {}
        for sale in sales:
            payment_breakdown[sale["payment_method"]] = payment_breakdown.get(sale["payment_method"], 0) + int(sale["net_cents"])
        return {
            "start": start,
            "end": end,
            "sales": sales,
            "transactions": transactions,
            "summary": {
                "gross_cents": gross,
                "fees_cents": fees,
                "net_cents": net,
                "income_cents": income,
                "expenses_cents": expenses,
                "profit_cents": income - expenses,
                "ticket_cents": gross // len(sales) if sales else 0,
                "effective_fee_bps": int(fees * 10_000 / gross) if gross else 0,
            },
            "payment_breakdown": payment_breakdown,
        }

    def search(self, user_id: int, term: str) -> list[sqlite3.Row]:
        pattern = f"%{term.strip()}%"
        results = self._all(
            """
            SELECT 'venda' AS record_type, id, occurred_on AS record_date, item_description AS title, net_cents AS amount_cents, status
            FROM sales WHERE user_id = ? AND (item_description LIKE ? OR notes LIKE ?)
            UNION ALL
            SELECT 'boleto' AS record_type, id, due_on AS record_date, description AS title, amount_cents, status
            FROM bills WHERE user_id = ? AND (description LIKE ? OR barcode LIKE ?)
            UNION ALL
            SELECT 'lancamento' AS record_type, id, occurred_on AS record_date, description AS title, amount_cents, kind AS status
            FROM transactions WHERE user_id = ? AND (description LIKE ? OR category LIKE ?)
            ORDER BY record_date DESC LIMIT 20
            """,
            (user_id, pattern, pattern, user_id, pattern, pattern, user_id, pattern, pattern),
        )
        return results

    def pending_alerts(self, user_id: int) -> list[tuple[str, str]]:
        settings = {row["alert_type"]: row for row in self.alert_settings(user_id)}
        messages: list[tuple[str, str]] = []
        current = today()
        bill_setting = settings.get("boleto")
        if bill_setting and bill_setting["enabled"]:
            limit = iso(current + timedelta(days=int(bill_setting["days_before"])))
            bills = self._all(
                """
                SELECT * FROM bills WHERE user_id = ? AND status IN ('aberto', 'agendado', 'vencido') AND due_on <= ?
                ORDER BY due_on LIMIT 10
                """,
                (user_id, limit),
            )
            for bill in bills:
                label = "vencido" if bill["due_on"] < iso(current) else f"vence em {bill['due_on']}"
                messages.append((f"boleto-{bill['id']}", f"Boleto {label}: {bill['description']}"))
        tax_setting = settings.get("imposto")
        if tax_setting and tax_setting["enabled"]:
            limit = iso(current + timedelta(days=int(tax_setting["days_before"])))
            taxes = self._all(
                """
                SELECT * FROM bills WHERE user_id = ? AND status IN ('aberto', 'agendado', 'vencido')
                AND due_on <= ? AND (LOWER(category) LIKE '%das%' OR LOWER(description) LIKE '%das%')
                ORDER BY due_on
                """,
                (user_id, limit),
            )
            for tax in taxes:
                messages.append((f"imposto-{tax['id']}", f"Imposto pendente: {tax['description']} vence em {tax['due_on']}."))
        receivable_setting = settings.get("recebimento")
        if receivable_setting and receivable_setting["enabled"]:
            limit = iso(current + timedelta(days=int(receivable_setting["days_before"])))
            installments = self._all(
                """
                SELECT si.*, s.id AS sale_id FROM sale_installments si JOIN sales s ON s.id = si.sale_id
                WHERE s.user_id = ? AND si.status = 'a_receber' AND si.expected_on <= ? ORDER BY si.expected_on
                """,
                (user_id, limit),
            )
            for installment in installments:
                messages.append(
                    (f"recebimento-{installment['id']}", f"Recebimento da venda #{installment['sale_id']} previsto para {installment['expected_on']}: {brl(installment['amount_cents'])}."),
                )
        limit_setting = settings.get("limite")
        user = self.user(user_id)
        annual_total = self.annual_sales_total(user_id)
        annual_limit = int(user["annual_limit_cents"])
        if limit_setting and limit_setting["enabled"] and annual_limit:
            ratio = annual_total / annual_limit
            for threshold in (0.9, 0.8, 0.7):
                if ratio >= threshold:
                    messages.append((f"limite-{int(threshold * 100)}", f"Faturamento em {ratio:.0%} do limite anual do MEI."))
                    break
        cash_setting = settings.get("caixa")
        dashboard = self.dashboard(user_id)
        if cash_setting and cash_setting["enabled"] and dashboard["forecasts"][30] < 0:
            messages.append(("caixa-negativo", "Previsao de caixa negativa para os proximos 30 dias."))
        fee_setting = settings.get("taxa")
        if fee_setting and fee_setting["enabled"]:
            recent_sales = self.list_sales(user_id, current.replace(day=1), current)
            for sale in recent_sales:
                if sale["machine_id"]:
                    machine = self.machine(user_id, int(sale["machine_id"]))
                    if int(sale["fee_bps"]) > int(machine["contracted_fee_bps"]):
                        messages.append((f"taxa-{sale['id']}", f"Venda #{sale['id']} teve taxa efetiva de {percentage(sale['fee_bps'])}, acima da contratada."))
        return messages

    def was_alert_delivered(self, user_id: int, alert_key: str) -> bool:
        row = self._one(
            "SELECT 1 FROM alert_deliveries WHERE user_id = ? AND alert_key = ? AND delivered_on = ?",
            (user_id, alert_key, iso(today())),
        )
        return row is not None

    def record_alert_delivery(self, user_id: int, alert_key: str) -> None:
        with self._transaction() as cursor:
            cursor.execute(
                "INSERT OR IGNORE INTO alert_deliveries (user_id, alert_key, delivered_on) VALUES (?, ?, ?)",
                (user_id, alert_key, iso(today())),
            )
