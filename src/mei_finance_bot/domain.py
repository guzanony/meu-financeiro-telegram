from __future__ import annotations

import calendar
import re
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP


class ValidationError(ValueError):
    """Erro de validacao exibido de forma amigavel pelo bot."""


PAYMENT_METHODS = {"pix", "dinheiro", "debito", "credito", "boleto"}
SALE_STATUSES = {"a_receber", "recebida", "cancelada", "estornada"}
BILL_STATUSES = {"aberto", "agendado", "pago", "vencido"}
ALERT_TYPES = {"boleto", "imposto", "limite", "recebimento", "caixa", "taxa"}


def today() -> date:
    return datetime.now().date()


def parse_date(value: str, *, field: str = "data") -> date:
    value = value.strip()
    for pattern in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            continue
    raise ValidationError(f"{field.capitalize()} invalida. Use DD/MM/AAAA.")


def parse_optional_date(value: str) -> date | None:
    return None if value.strip() in {"", "-"} else parse_date(value)


def parse_cents(value: str, *, field: str = "valor") -> int:
    raw = value.strip().lower().replace("r$", "").replace(" ", "")
    if not raw:
        raise ValidationError(f"Informe o {field}.")
    if raw.count(",") == 1:
        raw = raw.replace(".", "").replace(",", ".")
    try:
        amount = Decimal(raw).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception as error:
        raise ValidationError(f"{field.capitalize()} invalido. Exemplo: 1234,56.") from error
    if amount < 0:
        raise ValidationError(f"{field.capitalize()} nao pode ser negativo.")
    return int(amount * 100)


def parse_signed_cents(value: str, *, field: str = "valor") -> int:
    raw = value.strip().lower().replace("r$", "").replace(" ", "")
    if not raw:
        raise ValidationError(f"Informe o {field}.")
    if raw.count(",") == 1:
        raw = raw.replace(".", "").replace(",", ".")
    try:
        amount = Decimal(raw).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception as error:
        raise ValidationError(f"{field.capitalize()} invalido. Exemplo: -1234,56.") from error
    return int(amount * 100)


def parse_percentage_bps(value: str, *, field: str = "taxa") -> int:
    raw = value.strip().replace("%", "").replace(",", ".")
    try:
        percentage = Decimal(raw)
    except Exception as error:
        raise ValidationError(f"{field.capitalize()} invalida. Exemplo: 2,49.") from error
    if percentage < 0 or percentage > 100:
        raise ValidationError(f"{field.capitalize()} deve estar entre 0% e 100%.")
    return int((percentage * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def parse_positive_int(value: str, *, field: str, maximum: int = 999) -> int:
    try:
        parsed = int(value.strip())
    except ValueError as error:
        raise ValidationError(f"{field.capitalize()} deve ser um numero inteiro.") from error
    if parsed < 1 or parsed > maximum:
        raise ValidationError(f"{field.capitalize()} deve estar entre 1 e {maximum}.")
    return parsed


def normalize_payment_method(value: str) -> str:
    normalized = value.strip().lower().replace("é", "e")
    aliases = {"cartao": "credito", "cartao credito": "credito", "cartao debito": "debito"}
    normalized = aliases.get(normalized, normalized)
    if normalized not in PAYMENT_METHODS:
        valid = ", ".join(sorted(PAYMENT_METHODS))
        raise ValidationError(f"Forma de pagamento invalida. Use: {valid}.")
    return normalized


def normalize_barcode(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if len(digits) not in {44, 47, 48}:
        raise ValidationError("Linha digitavel invalida. Informe 44, 47 ou 48 digitos.")
    return digits


def brl(cents: int | None) -> str:
    cents = cents or 0
    prefix = "-" if cents < 0 else ""
    cents = abs(cents)
    major, minor = divmod(cents, 100)
    formatted = f"{major:,}".replace(",", ".")
    return f"{prefix}R$ {formatted},{minor:02d}"


def percentage(bps: int | None) -> str:
    return f"{(bps or 0) / 100:.2f}".replace(".", ",") + "%"


def add_months(source: date, months: int) -> date:
    month_index = source.month - 1 + months
    year = source.year + month_index // 12
    month = month_index % 12 + 1
    day = min(source.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def split_cents(total: int, parts: int) -> list[int]:
    base, remainder = divmod(total, parts)
    return [base + (1 if index < remainder else 0) for index in range(parts)]


def iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def human_date(value: str | date | None) -> str:
    if not value:
        return "-"
    if isinstance(value, str):
        value = datetime.fromisoformat(value).date()
    return value.strftime("%d/%m/%Y")
