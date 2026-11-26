from __future__ import annotations

import json
import mimetypes
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .database import FinanceRepository
from .domain import (
    ALERT_TYPES,
    ValidationError,
    brl,
    human_date,
    normalize_barcode,
    normalize_payment_method,
    parse_cents,
    parse_date,
    parse_optional_date,
    parse_percentage_bps,
    parse_positive_int,
    parse_signed_cents,
    percentage,
    today,
)
from .reports import ReportExporter


class TelegramApiError(RuntimeError):
    pass


class TelegramClient:
    def __init__(self, token: str) -> None:
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.file_url = f"https://api.telegram.org/file/bot{token}"

    def _call(self, method: str, payload: dict[str, Any] | None = None, timeout: int = 35) -> Any:
        request = Request(
            f"{self.base_url}/{method}",
            data=json.dumps(payload or {}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError) as error:
            raise TelegramApiError(f"Falha ao chamar Telegram ({method}): {error}") from error
        if not result.get("ok"):
            raise TelegramApiError(result.get("description", f"Falha no metodo {method}"))
        return result["result"]

    def updates(self, offset: int | None) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": 30, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        return self._call("getUpdates", payload, timeout=40)

    def send_message(self, chat_id: int, text: str, keyboard: list[list[str]] | None = None) -> None:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text[:4096]}
        if keyboard:
            payload["reply_markup"] = {"keyboard": keyboard, "resize_keyboard": True}
        self._call("sendMessage", payload)

    def _multipart(self, method: str, fields: dict[str, str], file_key: str, path: Path) -> Any:
        boundary = f"----mei-finance-{uuid.uuid4().hex}"
        chunks: list[bytes] = []
        for key, value in fields.items():
            chunks.append(f"--{boundary}\r\n".encode())
            chunks.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(
            f'Content-Disposition: form-data; name="{file_key}"; filename="{path.name}"\r\nContent-Type: {mime}\r\n\r\n'.encode()
        )
        chunks.append(path.read_bytes())
        chunks.append(f"\r\n--{boundary}--\r\n".encode())
        request = Request(
            f"{self.base_url}/{method}",
            data=b"".join(chunks),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=60) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError) as error:
            raise TelegramApiError(f"Falha ao enviar arquivo: {error}") from error
        if not result.get("ok"):
            raise TelegramApiError(result.get("description", "Telegram recusou o arquivo"))
        return result["result"]

    def send_document(self, chat_id: int, path: Path, caption: str) -> None:
        self._multipart("sendDocument", {"chat_id": str(chat_id), "caption": caption[:1024]}, "document", path)

    def file_bytes(self, file_id: str) -> bytes:
        metadata = self._call("getFile", {"file_id": file_id})
        request = Request(f"{self.file_url}/{metadata['file_path']}")
        try:
            with urlopen(request, timeout=60) as response:
                return response.read()
        except (HTTPError, URLError) as error:
            raise TelegramApiError(f"Nao foi possivel baixar o arquivo: {error}") from error


@dataclass(frozen=True)
class Field:
    key: str
    prompt: str
    optional: bool = False


@dataclass
class Flow:
    name: str
    fields: list[Field] = field(default_factory=list)
    values: dict[str, str | None] = field(default_factory=dict)
    index: int = 0
    entity_type: str | None = None
    entity_id: int | None = None


MENU = [["Saldo", "Nova venda", "Novo boleto"], ["Lancamento", "Relatorios", "Alertas"], ["Ajuda"]]
BUTTON_COMMANDS = {
    "Saldo": "/saldo", "Nova venda": "/venda", "Novo boleto": "/boleto", "Lancamento": "/lancamento",
    "Relatorios": "/relatorio mensal", "Alertas": "/alertas", "Ajuda": "/ajuda",
}


FORMS: dict[str, list[Field]] = {
    "sale": [
        Field("occurred_on", "Data da venda (DD/MM/AAAA ou - para hoje)", True),
        Field("gross", "Valor bruto da venda (ex.: 120,50)"),
        Field("payment", "Forma de pagamento: pix, dinheiro, debito, credito ou boleto"),
        Field("machine_id", "ID da maquininha (ou - se nao usou)", True),
        Field("installments", "Numero de parcelas (ou - para 1)", True),
        Field("expected_on", "Primeiro recebimento (DD/MM/AAAA ou - para calcular)", True),
        Field("client_id", "ID do cliente (ou -)", True),
        Field("product_id", "ID do produto (ou -)", True),
        Field("description", "Descricao do item/servico (ou - se usou produto)", True),
        Field("quantity", "Quantidade (ou - para 1)", True),
        Field("discount", "Desconto em R$ (ou - para zero)", True),
        Field("notes", "Observacoes (ou -)", True),
    ],
    "machine": [
        Field("name", "Nome da maquininha/adquirente"),
        Field("debit", "Taxa no debito (%)"),
        Field("credit", "Taxa no credito a vista (%)"),
        Field("installment", "Acrescimo de taxa por parcela (%)"),
        Field("anticipation", "Taxa de antecipacao (%)"),
    ],
    "bill": [
        Field("kind", "Tipo: pagar ou receber"),
        Field("description", "Descricao do boleto/conta"),
        Field("amount", "Valor (ex.: 234,90)"),
        Field("due_on", "Vencimento (DD/MM/AAAA)"),
        Field("category", "Categoria (ex.: aluguel, DAS-MEI, fornecedor ou -)", True),
        Field("barcode", "Linha digitavel (ou -; use /ler_boleto para foto)", True),
        Field("recurrence", "Recorrencia em meses (1 para mensal, ou -)", True),
    ],
    "transaction": [
        Field("kind", "Tipo: entrada ou saida"),
        Field("description", "Descricao"),
        Field("amount", "Valor"),
        Field("occurred_on", "Data (DD/MM/AAAA ou - para hoje)", True),
        Field("category", "Categoria (ou -)", True),
    ],
    "account": [
        Field("name", "Nome da conta (banco, carteira digital, caixa)"),
        Field("kind", "Tipo da conta"),
        Field("initial_balance", "Saldo inicial (use 0 se nao houver)"),
    ],
    "client": [
        Field("name", "Nome do cliente"), Field("document", "CPF/CNPJ (ou -)", True),
        Field("phone", "Telefone (ou -)", True), Field("email", "E-mail (ou -)", True),
    ],
    "product": [
        Field("name", "Nome do produto/servico"), Field("price", "Preco padrao"), Field("sku", "Codigo/SKU (ou -)", True),
    ],
}


class FinanceBot:
    def __init__(
        self,
        telegram: TelegramClient,
        repository: FinanceRepository,
        exporter: ReportExporter,
        files_path: Path,
        annual_limit_cents: int,
        allowed_user_ids: frozenset[int],
    ) -> None:
        self.telegram = telegram
        self.repository = repository
        self.exporter = exporter
        self.files_path = files_path
        self.annual_limit_cents = annual_limit_cents
        self.allowed_user_ids = allowed_user_ids
        self.flows: dict[int, Flow] = {}

    def send(self, chat_id: int, text: str, *, menu: bool = False) -> None:
        self.telegram.send_message(chat_id, text, MENU if menu else None)

    def handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message")
        if not message or not message.get("from"):
            return
        actor_id = int(message["from"]["id"])
        chat_id = int(message["chat"]["id"])
        if self.allowed_user_ids and actor_id not in self.allowed_user_ids:
            self.send(chat_id, "Acesso nao autorizado. Peca ao administrador para liberar seu Telegram ID.")
            return
        full_name = " ".join(part for part in [message["from"].get("first_name", ""), message["from"].get("last_name", "")] if part)
        self.repository.ensure_user(actor_id, full_name, self.annual_limit_cents)
        try:
            if message.get("photo") or message.get("document"):
                self._handle_media(actor_id, chat_id, message)
                return
            raw_text = (message.get("text") or "").strip()
            if not raw_text:
                self.send(chat_id, "Envie um comando ou use o menu. /ajuda mostra todas as opcoes.", menu=True)
                return
            raw_text = BUTTON_COMMANDS.get(raw_text, raw_text)
            if raw_text.startswith("/"):
                self._handle_command(actor_id, chat_id, raw_text)
            elif actor_id in self.flows:
                self._handle_flow_text(actor_id, chat_id, raw_text)
            else:
                self.send(chat_id, "Nao entendi. Use /ajuda ou o menu.", menu=True)
        except (ValidationError, ValueError, RuntimeError) as error:
            self.send(chat_id, f"⚠️ {error}")
        except TelegramApiError:
            raise
        except Exception as error:
            self.send(chat_id, "Ocorreu um erro ao processar a solicitacao. Tente novamente ou use /cancelar.")
            print(f"Erro nao tratado: {error}")

    def _handle_command(self, user_id: int, chat_id: int, raw: str) -> None:
        command, _, argument_text = raw.partition(" ")
        command = command.split("@", 1)[0].lower()
        args = argument_text.split()
        if command in {"/start", "/inicio"}:
            self._dashboard(user_id, chat_id, greet=command == "/start")
        elif command == "/ajuda":
            self.send(chat_id, self._help(), menu=True)
        elif command == "/cancelar":
            self.flows.pop(user_id, None)
            self.send(chat_id, "Operacao cancelada.", menu=True)
        elif command == "/saldo":
            self._dashboard(user_id, chat_id)
        elif command == "/venda":
            self._begin(user_id, chat_id, "sale", "Nova venda")
        elif command == "/vendas":
            self._sales(user_id, chat_id)
        elif command == "/venda_status":
            self._change_sale_status(user_id, chat_id, args)
        elif command == "/estornar":
            self._change_sale_status(user_id, chat_id, [*args, "estornada"])
        elif command == "/cancelar_venda":
            self._change_sale_status(user_id, chat_id, [*args, "cancelada"])
        elif command == "/antecipar":
            self._anticipate_sale(user_id, chat_id, args)
        elif command == "/editar_venda":
            self._edit_sale(user_id, chat_id, args)
        elif command == "/nota":
            self._edit_sale(user_id, chat_id, [*args[:1], "nota", *args[1:]])
        elif command == "/recibo":
            self._send_receipt(user_id, chat_id, args)
        elif command == "/maquininha":
            self._begin(user_id, chat_id, "machine", "Nova maquininha")
        elif command == "/maquininhas":
            self._machines(user_id, chat_id)
        elif command == "/boleto":
            self._begin(user_id, chat_id, "bill", "Novo boleto")
        elif command == "/boletos":
            self._bills(user_id, chat_id, args[0] if args else None)
        elif command == "/baixar":
            self._pay_bill(user_id, chat_id, args)
        elif command == "/agendar_boleto":
            self._schedule_bill(user_id, chat_id, args)
        elif command == "/ler_boleto":
            self.flows[user_id] = Flow(name="scan")
            self.send(chat_id, "Envie uma foto legivel do codigo de barras ou cole a linha digitavel. Envie /cancelar para sair.")
        elif command == "/lancamento":
            self._begin(user_id, chat_id, "transaction", "Novo lancamento")
        elif command == "/conta":
            self._begin(user_id, chat_id, "account", "Nova conta")
        elif command == "/contas":
            self._accounts(user_id, chat_id)
        elif command == "/cliente":
            self._begin(user_id, chat_id, "client", "Novo cliente")
        elif command == "/clientes":
            self._clients(user_id, chat_id)
        elif command == "/produto":
            self._begin(user_id, chat_id, "product", "Novo produto")
        elif command == "/produtos":
            self._products(user_id, chat_id)
        elif command == "/impostos":
            self._taxes(user_id, chat_id)
        elif command == "/limite":
            self._set_limit(user_id, chat_id, args)
        elif command == "/alertas":
            self._alerts(user_id, chat_id)
        elif command == "/alertar":
            self._configure_alert(user_id, chat_id, args)
        elif command == "/contador":
            self._set_accountant(user_id, chat_id, argument_text)
        elif command == "/relatorio":
            self._report_summary(user_id, chat_id, args)
        elif command == "/exportar":
            self._export_report(user_id, chat_id, args)
        elif command == "/agendar_relatorio":
            self._configure_report_schedule(user_id, chat_id, args)
        elif command == "/anexar":
            self._begin_attachment(user_id, chat_id, args)
        elif command == "/buscar":
            self._search(user_id, chat_id, argument_text)
        else:
            self.send(chat_id, "Comando nao encontrado. Use /ajuda.")

    def _begin(self, user_id: int, chat_id: int, form_name: str, title: str) -> None:
        self.flows[user_id] = Flow(name=form_name, fields=FORMS[form_name])
        self.send(chat_id, f"{title}. Envie /cancelar para desistir.\n\n{FORMS[form_name][0].prompt}")

    def _handle_flow_text(self, user_id: int, chat_id: int, text: str) -> None:
        flow = self.flows[user_id]
        if flow.name == "scan":
            barcode = normalize_barcode(text)
            self.flows.pop(user_id, None)
            self.send(chat_id, f"Linha digitavel validada: {barcode}\nAgora use /boleto e informe essa linha no campo solicitado.")
            return
        if flow.name == "attachment":
            self.send(chat_id, "Envie uma foto ou documento para concluir o anexo. Use /cancelar para desistir.")
            return
        field = flow.fields[flow.index]
        value: str | None = None if field.optional and text.strip() in {"", "-"} else text.strip()
        if not value and not field.optional:
            raise ValidationError("Este campo e obrigatorio.")
        flow.values[field.key] = value
        flow.index += 1
        if flow.index < len(flow.fields):
            self.send(chat_id, flow.fields[flow.index].prompt)
            return
        try:
            self._finish_flow(user_id, chat_id, flow)
        except Exception:
            self.flows.pop(user_id, None)
            raise
        self.flows.pop(user_id, None)

    def _finish_flow(self, user_id: int, chat_id: int, flow: Flow) -> None:
        values = flow.values
        if flow.name == "sale":
            occurred_on = parse_optional_date(values["occurred_on"] or "-") or today()
            payment = normalize_payment_method(values["payment"] or "")
            gross = parse_cents(values["gross"] or "")
            discount = parse_cents(values["discount"] or "0") if values["discount"] else 0
            installments = parse_positive_int(values["installments"] or "1", field="parcelas", maximum=48)
            machine_id = int(values["machine_id"]) if values["machine_id"] else None
            client_id = int(values["client_id"]) if values["client_id"] else None
            product_id = int(values["product_id"]) if values["product_id"] else None
            expected_on = parse_optional_date(values["expected_on"] or "-") or occurred_on
            sale = self.repository.create_sale(
                user_id,
                occurred_on=occurred_on,
                gross_cents=gross,
                payment_method=payment,
                machine_id=machine_id,
                installments=installments,
                expected_on=expected_on,
                client_id=client_id,
                product_id=product_id,
                item_description=values["description"],
                quantity=parse_positive_int(values["quantity"] or "1", field="quantidade"),
                discount_cents=discount,
                notes=values["notes"],
            )
            self.send(
                chat_id,
                f"✅ Venda #{sale['id']} registrada.\nBruto: {brl(sale['gross_cents'])}\nTaxa: {brl(sale['fee_cents'])} ({percentage(sale['fee_bps'])})\nLiquido: {brl(sale['net_cents'])}\nStatus: {sale['status']}",
                menu=True,
            )
        elif flow.name == "machine":
            machine_id = self.repository.create_machine(
                user_id, values["name"] or "", parse_percentage_bps(values["debit"] or ""),
                parse_percentage_bps(values["credit"] or ""), parse_percentage_bps(values["installment"] or ""),
                parse_percentage_bps(values["anticipation"] or ""),
            )
            self.send(chat_id, f"✅ Maquininha #{machine_id} cadastrada.", menu=True)
        elif flow.name == "bill":
            kind = (values["kind"] or "").lower()
            if kind not in {"pagar", "receber"}:
                raise ValidationError("Tipo deve ser pagar ou receber.")
            recurrence = parse_positive_int(values["recurrence"], field="recorrencia", maximum=120) if values["recurrence"] else None
            bill = self.repository.create_bill(
                user_id, kind=kind, description=values["description"] or "", amount_cents=parse_cents(values["amount"] or ""),
                due_on=parse_date(values["due_on"] or ""), category=values["category"], barcode=values["barcode"], recurrence_months=recurrence,
            )
            self.send(chat_id, f"✅ Boleto #{bill['id']} registrado para {human_date(bill['due_on'])}.\nUse /baixar {bill['id']} quando ele for pago.", menu=True)
        elif flow.name == "transaction":
            kind = (values["kind"] or "").lower()
            transaction_id = self.repository.create_transaction(
                user_id, kind, values["description"] or "", parse_cents(values["amount"] or ""),
                parse_optional_date(values["occurred_on"] or "-") or today(), values["category"],
            )
            self.send(chat_id, f"✅ Lancamento #{transaction_id} registrado.", menu=True)
        elif flow.name == "account":
            account_id = self.repository.create_account(
                user_id, values["name"] or "", values["kind"] or "", parse_signed_cents(values["initial_balance"] or "0"),
            )
            self.send(chat_id, f"✅ Conta #{account_id} cadastrada.", menu=True)
        elif flow.name == "client":
            client_id = self.repository.create_client(user_id, values["name"] or "", values["document"], values["phone"], values["email"])
            self.send(chat_id, f"✅ Cliente #{client_id} cadastrado.", menu=True)
        elif flow.name == "product":
            product_id = self.repository.create_product(user_id, values["name"] or "", parse_cents(values["price"] or ""), values["sku"])
            self.send(chat_id, f"✅ Produto/servico #{product_id} cadastrado.", menu=True)

    def _handle_media(self, user_id: int, chat_id: int, message: dict[str, Any]) -> None:
        flow = self.flows.get(user_id)
        if not flow or flow.name not in {"attachment", "scan"}:
            self.send(chat_id, "Para anexar, use /anexar boleto ID ou /anexar venda ID antes de enviar o arquivo.")
            return
        file_id, original_name, mime_type = self._telegram_file_data(message)
        contents = self.telegram.file_bytes(file_id)
        suffix = Path(original_name).suffix or (".jpg" if message.get("photo") else "")
        safe_name = f"{user_id}_{int(time.time())}_{uuid.uuid4().hex[:8]}{suffix}"
        target = self.files_path / safe_name
        target.write_bytes(contents)
        if flow.name == "scan":
            barcode = self._barcode_from_image(target)
            if barcode:
                self.flows.pop(user_id, None)
                self.send(chat_id, f"✅ Linha digitavel identificada: {barcode}\nUse /boleto para registrar o titulo.")
            else:
                target.unlink(missing_ok=True)
                self.send(chat_id, "Nao consegui ler a linha digitavel. Envie os 44, 47 ou 48 digitos como texto.")
            return
        attachment_id = self.repository.add_attachment(
            user_id, flow.entity_type or "", flow.entity_id or 0, str(target), file_id, mime_type,
        )
        self.flows.pop(user_id, None)
        self.send(chat_id, f"✅ Anexo #{attachment_id} salvo para {flow.entity_type} #{flow.entity_id}.", menu=True)

    @staticmethod
    def _telegram_file_data(message: dict[str, Any]) -> tuple[str, str, str | None]:
        if message.get("document"):
            document = message["document"]
            return document["file_id"], document.get("file_name", "documento"), document.get("mime_type")
        photo = message["photo"][-1]
        return photo["file_id"], "foto.jpg", "image/jpeg"

    @staticmethod
    def _barcode_from_image(path: Path) -> str | None:
        try:
            import pytesseract
            from PIL import Image
            text = pytesseract.image_to_string(Image.open(path), lang="por")
        except Exception:
            return None
        candidates = re.findall(r"(?:\d[ .-]?){44,50}", text)
        for candidate in candidates:
            try:
                return normalize_barcode(candidate)
            except ValidationError:
                continue
        return None

    def _dashboard(self, user_id: int, chat_id: int, greet: bool = False) -> None:
        data = self.repository.dashboard(user_id)
        account_lines = [f"- {account['name']}: {brl(account['balance_cents'])}" for account in data["accounts"]]
        text = "\n".join(
            [
                "👋 Bem-vindo ao Meu Financeiro." if greet else "💰 Saldo e previsao de caixa",
                f"Saldo total: {brl(data['total_balance_cents'])}",
                *(account_lines or ["- Nenhuma conta cadastrada"]),
                f"A receber: {brl(data['receivable_cents'])}",
                "Previsao:",
                f"- 7 dias: {brl(data['forecasts'][7])}",
                f"- 15 dias: {brl(data['forecasts'][15])}",
                f"- 30 dias: {brl(data['forecasts'][30])}",
                f"Seu Telegram ID: {user_id}",
            ]
        )
        self.send(chat_id, text, menu=True)

    def _sales(self, user_id: int, chat_id: int) -> None:
        sales = self.repository.list_sales(user_id)[:20]
        if not sales:
            self.send(chat_id, "Nenhuma venda registrada. Use /venda.")
            return
        lines = ["📋 Ultimas vendas"]
        for sale in sales:
            item = sale["item_description"] or sale["product_name"] or "Venda"
            lines.append(f"#{sale['id']} {human_date(sale['occurred_on'])} - {item}: {brl(sale['net_cents'])} ({sale['status']})")
        self.send(chat_id, "\n".join(lines))

    def _change_sale_status(self, user_id: int, chat_id: int, args: list[str]) -> None:
        if len(args) < 2:
            raise ValidationError("Use /venda_status ID recebida, /estornar ID ou /cancelar_venda ID.")
        sale = self.repository.set_sale_status(user_id, int(args[0]), args[1].lower())
        self.send(chat_id, f"✅ Venda #{sale['id']} atualizada para {sale['status']}.")

    def _anticipate_sale(self, user_id: int, chat_id: int, args: list[str]) -> None:
        if len(args) != 1:
            raise ValidationError("Use /antecipar ID.")
        sale, fee_cents = self.repository.anticipate_sale(user_id, int(args[0]))
        self.send(chat_id, f"✅ Venda #{sale['id']} antecipada. Taxa de antecipacao: {brl(fee_cents)}. Liquido final: {brl(sale['net_cents'])}.")

    def _edit_sale(self, user_id: int, chat_id: int, args: list[str]) -> None:
        if len(args) < 3:
            raise ValidationError("Use /editar_venda ID descricao|observacoes|nota NOVO_VALOR.")
        sale = self.repository.update_sale_field(user_id, int(args[0]), args[1], " ".join(args[2:]))
        self.send(chat_id, f"✅ Venda #{sale['id']} atualizada.")

    def _send_receipt(self, user_id: int, chat_id: int, args: list[str]) -> None:
        if len(args) != 1:
            raise ValidationError("Use /recibo ID.")
        sale = self.repository.sale(user_id, int(args[0]))
        path = self.exporter.receipt(user_id, sale, self.repository.sale_installments(user_id, int(args[0])))
        self.telegram.send_document(chat_id, path, f"Recibo da venda #{sale['id']}")

    def _machines(self, user_id: int, chat_id: int) -> None:
        machines = self.repository.list_machines(user_id)
        if not machines:
            self.send(chat_id, "Nenhuma maquininha cadastrada. Use /maquininha.")
            return
        lines = ["💳 Maquininhas"]
        for machine in machines:
            lines.append(
                f"#{machine['id']} {machine['name']} - debito {percentage(machine['debit_fee_bps'])}, credito {percentage(machine['credit_fee_bps'])}, +parcela {percentage(machine['installment_fee_bps'])}"
            )
        self.send(chat_id, "\n".join(lines))

    def _bills(self, user_id: int, chat_id: int, status: str | None) -> None:
        bills = self.repository.list_bills(user_id, status)
        if not bills:
            self.send(chat_id, "Nenhum boleto encontrado. Use /boleto.")
            return
        lines = ["🧾 Boletos"]
        for bill in bills[:30]:
            lines.append(f"#{bill['id']} {bill['kind']} {human_date(bill['due_on'])} - {bill['description']}: {brl(bill['amount_cents'])} ({bill['status']})")
        self.send(chat_id, "\n".join(lines))

    def _pay_bill(self, user_id: int, chat_id: int, args: list[str]) -> None:
        if not args:
            raise ValidationError("Use /baixar ID [juros].")
        interest = parse_cents(args[1], field="juros") if len(args) > 1 else 0
        bill = self.repository.mark_bill_paid(user_id, int(args[0]), interest)
        self.send(chat_id, f"✅ Boleto #{bill['id']} baixado como pago. Use /anexar boleto {bill['id']} para guardar o comprovante.")

    def _schedule_bill(self, user_id: int, chat_id: int, args: list[str]) -> None:
        if len(args) != 1:
            raise ValidationError("Use /agendar_boleto ID.")
        bill = self.repository.schedule_bill(user_id, int(args[0]))
        self.send(chat_id, f"✅ Boleto #{bill['id']} marcado como agendado.")

    def _accounts(self, user_id: int, chat_id: int) -> None:
        accounts = self.repository.list_accounts(user_id)
        self.send(chat_id, "\n".join(["🏦 Contas", *[f"#{item['id']} {item['name']} ({item['kind']}): {brl(item['balance_cents'])}" for item in accounts]]))

    def _clients(self, user_id: int, chat_id: int) -> None:
        clients = self.repository.list_clients(user_id)
        self.send(chat_id, "\n".join(["👥 Clientes", *[f"#{item['id']} {item['name']} - {item['phone'] or '-'}" for item in clients[:30]]]) if clients else "Nenhum cliente. Use /cliente.")

    def _products(self, user_id: int, chat_id: int) -> None:
        products = self.repository.list_products(user_id)
        self.send(chat_id, "\n".join(["📦 Produtos e servicos", *[f"#{item['id']} {item['name']}: {brl(item['price_cents'])}" for item in products[:30]]]) if products else "Nenhum produto. Use /produto.")

    def _taxes(self, user_id: int, chat_id: int) -> None:
        user = self.repository.user(user_id)
        annual_total = self.repository.annual_sales_total(user_id)
        limit = int(user["annual_limit_cents"])
        das_bills = [bill for bill in self.repository.list_bills(user_id) if "das" in (bill["category"] or "").lower() or "das" in bill["description"].lower()]
        lines = [
            "🧮 Impostos e limite MEI",
            f"Faturamento anual: {brl(annual_total)} de {brl(limit)} ({annual_total / limit:.1%})" if limit else f"Faturamento anual: {brl(annual_total)}",
            "DAS-MEI/DASN-SIMEI registrados:",
            *[f"- #{bill['id']} {bill['description']} - {human_date(bill['due_on'])} ({bill['status']})" for bill in das_bills[:10]],
            "Use /boleto e categoria DAS-MEI para controlar vencimentos. Use /limite VALOR para ajustar o teto.",
        ]
        self.send(chat_id, "\n".join(lines))

    def _set_limit(self, user_id: int, chat_id: int, args: list[str]) -> None:
        if not args:
            raise ValidationError("Use /limite 81000,00.")
        limit = parse_cents(args[0], field="limite")
        self.repository.set_annual_limit(user_id, limit)
        self.send(chat_id, f"✅ Limite anual configurado para {brl(limit)}.")

    def _alerts(self, user_id: int, chat_id: int) -> None:
        settings = self.repository.alert_settings(user_id)
        pending = self.repository.pending_alerts(user_id)
        lines = ["🔔 Central de alertas", "Configuracao:"]
        lines.extend([f"- {row['alert_type']}: {'ativo' if row['enabled'] else 'inativo'}, antecedencia {row['days_before']} dia(s)" for row in settings])
        lines.append("Pendentes:")
        lines.extend([f"- {message}" for _, message in pending] or ["- Nenhum alerta no momento"])
        lines.append("Altere com: /alertar boleto 7 on")
        self.send(chat_id, "\n".join(lines))

    def _configure_alert(self, user_id: int, chat_id: int, args: list[str]) -> None:
        if len(args) != 3:
            raise ValidationError("Use /alertar TIPO DIAS on|off. Tipos: boleto, imposto, limite, recebimento, caixa, taxa.")
        alert_type, days, state = args[0].lower(), args[1], args[2].lower()
        if alert_type not in ALERT_TYPES or state not in {"on", "off"}:
            raise ValidationError("Parametros invalidos para alerta.")
        self.repository.update_alert(user_id, alert_type, state == "on", parse_positive_int(days, field="dias", maximum=365))
        self.send(chat_id, f"✅ Alerta {alert_type} atualizado.")

    def _set_accountant(self, user_id: int, chat_id: int, email: str) -> None:
        email = email.strip()
        if email and "@" not in email:
            raise ValidationError("Informe um e-mail valido ou apenas /contador para limpar.")
        self.repository.set_accountant_email(user_id, email or None)
        self.send(chat_id, "✅ E-mail do contador atualizado." if email else "E-mail do contador removido.")

    def _report_period(self, period: str) -> tuple[date, date]:
        current = today()
        if period == "diario":
            return current, current
        if period == "mensal":
            return current.replace(day=1), current
        if period == "anual":
            return current.replace(month=1, day=1), current
        raise ValidationError("Periodo deve ser diario, mensal ou anual.")

    def _report_summary(self, user_id: int, chat_id: int, args: list[str]) -> None:
        start, end = self._report_period(args[0].lower() if args else "mensal")
        data = self.repository.report_data(user_id, start, end)
        self.send(chat_id, "📊 " + self.exporter.overview(data) + "\n\nPara baixar: /exportar pdf|xlsx|csv [inicio] [fim]")

    def _export_report(self, user_id: int, chat_id: int, args: list[str]) -> None:
        if not args or args[0].lower() not in {"csv", "xlsx", "pdf"}:
            raise ValidationError("Use /exportar csv|xlsx|pdf [DD/MM/AAAA] [DD/MM/AAAA].")
        export_type = args[0].lower()
        default_start, default_end = self._report_period("mensal")
        start = parse_date(args[1]) if len(args) > 1 else default_start
        end = parse_date(args[2]) if len(args) > 2 else default_end
        if start > end:
            raise ValidationError("A data inicial deve ser anterior a final.")
        data = self.repository.report_data(user_id, start, end)
        path = getattr(self.exporter, export_type)(user_id, data)
        self.telegram.send_document(chat_id, path, f"Relatorio {human_date(start)} a {human_date(end)}")

    def _configure_report_schedule(self, user_id: int, chat_id: int, args: list[str]) -> None:
        if len(args) != 3:
            raise ValidationError("Use /agendar_relatorio diario|semanal|mensal|anual telegram|contador on|off.")
        period, destination, state = (item.lower() for item in args)
        if destination == "contador" and state == "on" and not self.repository.user(user_id)["accountant_email"]:
            raise ValidationError("Defina primeiro o e-mail com /contador email@empresa.com.")
        self.repository.update_report_schedule(user_id, period, destination, state == "on")
        self.send(chat_id, f"✅ Relatorio {period} para {destination}: {'ativo' if state == 'on' else 'inativo'}.")

    def _begin_attachment(self, user_id: int, chat_id: int, args: list[str]) -> None:
        if len(args) != 2 or args[0].lower() not in {"boleto", "venda"}:
            raise ValidationError("Use /anexar boleto ID ou /anexar venda ID.")
        entity_type = "bill" if args[0].lower() == "boleto" else "sale"
        entity_id = int(args[1])
        if entity_type == "bill":
            self.repository.bill(user_id, entity_id)
        else:
            self.repository.sale(user_id, entity_id)
        self.flows[user_id] = Flow(name="attachment", entity_type=entity_type, entity_id=entity_id)
        self.send(chat_id, "Envie agora a foto ou o documento. Envie /cancelar para desistir.")

    def _search(self, user_id: int, chat_id: int, term: str) -> None:
        if not term.strip():
            raise ValidationError("Use /buscar TERMO.")
        records = self.repository.search(user_id, term)
        if not records:
            self.send(chat_id, "Nenhum registro encontrado.")
            return
        lines = ["🔎 Resultados"]
        for record in records:
            lines.append(f"{record['record_type']} #{record['id']} - {human_date(record['record_date'])}: {record['title'] or '-'} {brl(record['amount_cents'])} ({record['status']})")
        self.send(chat_id, "\n".join(lines))

    @staticmethod
    def _help() -> str:
        return """ℹ️ Comandos principais

/saldo - saldo e previsao
/venda, /vendas - registrar e consultar vendas
/venda_status ID recebida, /antecipar ID - recebimento
/editar_venda ID campo texto, /nota ID numero, /recibo ID
/maquininha, /maquininhas - taxas de cartao
/boleto, /boletos, /baixar ID, /agendar_boleto ID - contas e boletos
/ler_boleto - ler foto da linha digitavel
/lancamento, /conta, /contas - caixa e contas
/cliente, /clientes, /produto, /produtos - cadastros
/impostos, /limite VALOR - MEI e DAS
/alertas, /alertar TIPO DIAS on|off - notificacoes
/relatorio diario|mensal|anual - resumo
/exportar pdf|xlsx|csv [inicio] [fim] - arquivo
/agendar_relatorio periodo destino on|off - envio automatico
/anexar boleto|venda ID - comprovante/documento
/buscar TERMO - busca rapida
/cancelar - parar um cadastro em andamento"""

    def send_pending_alerts(self) -> None:
        for user_id in self.repository.all_user_ids():
            for alert_key, message in self.repository.pending_alerts(user_id):
                if self.repository.was_alert_delivered(user_id, alert_key):
                    continue
                self.send(user_id, f"🔔 {message}")
                self.repository.record_alert_delivery(user_id, alert_key)

    def send_scheduled_reports(self) -> list[tuple[str, Path]]:
        """Envia os relatorios agendados no Telegram e devolve os destinados ao contador."""
        accountant_deliveries: list[tuple[str, Path]] = []
        current = today()
        for schedule in self.repository.due_report_schedules(current):
            period = schedule["period"]
            if period == "diario":
                start, end = current, current
            elif period == "semanal":
                start, end = current - timedelta(days=7), current
            elif period == "mensal":
                start, end = current.replace(day=1), current
            else:
                start, end = current.replace(month=1, day=1), current
            user_id = int(schedule["user_id"])
            data = self.repository.report_data(user_id, start, end)
            path = self.exporter.pdf(user_id, data)
            if schedule["send_to_telegram"]:
                self.telegram.send_document(user_id, path, f"Relatorio automatico {period}")
            if schedule["send_to_accountant"]:
                user = self.repository.user(user_id)
                if user["accountant_email"]:
                    accountant_deliveries.append((str(user["accountant_email"]), path))
            self.repository.mark_report_schedule_sent(user_id, period, current)
        return accountant_deliveries
