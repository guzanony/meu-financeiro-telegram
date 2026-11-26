from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Any

from .domain import brl, human_date, percentage


class ReportExporter:
    def __init__(self, reports_path: Path) -> None:
        self.reports_path = reports_path
        self.reports_path.mkdir(parents=True, exist_ok=True)

    def _path(self, user_id: int, start: date, end: date, extension: str) -> Path:
        folder = self.reports_path / str(user_id)
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"relatorio_{start.isoformat()}_{end.isoformat()}.{extension}"

    @staticmethod
    def overview(data: dict[str, Any]) -> str:
        summary = data["summary"]
        payment_lines = [f"- {method.title()}: {brl(amount)}" for method, amount in data["payment_breakdown"].items()]
        return "\n".join(
            [
                f"Relatorio de {human_date(data['start'])} a {human_date(data['end'])}",
                f"Faturamento bruto: {brl(summary['gross_cents'])}",
                f"Taxas: {brl(summary['fees_cents'])} ({percentage(summary['effective_fee_bps'])})",
                f"Liquido das vendas: {brl(summary['net_cents'])}",
                f"Entradas realizadas: {brl(summary['income_cents'])}",
                f"Despesas realizadas: {brl(summary['expenses_cents'])}",
                f"Resultado de caixa: {brl(summary['profit_cents'])}",
                f"Ticket medio: {brl(summary['ticket_cents'])}",
                "Por pagamento:",
                *(payment_lines or ["- Sem vendas no periodo"]),
            ]
        )

    def csv(self, user_id: int, data: dict[str, Any]) -> Path:
        path = self._path(user_id, data["start"], data["end"], "csv")
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream, delimiter=";")
            writer.writerow(["Resumo"])
            for key, value in data["summary"].items():
                writer.writerow([key, value])
            writer.writerow([])
            writer.writerow(["Vendas"])
            writer.writerow(
                ["ID", "Data", "Item", "Cliente", "Pagamento", "Bruto", "Desconto", "Taxa", "Liquido", "Parcelas", "Status"]
            )
            for sale in data["sales"]:
                writer.writerow(
                    [
                        sale["id"], sale["occurred_on"], sale["item_description"] or sale["product_name"] or "Venda",
                        sale["client_name"] or "", sale["payment_method"], sale["gross_cents"], sale["discount_cents"],
                        sale["fee_cents"], sale["net_cents"], sale["installments"], sale["status"],
                    ]
                )
            writer.writerow([])
            writer.writerow(["Lancamentos"])
            writer.writerow(["Data", "Tipo", "Descricao", "Categoria", "Valor"])
            for transaction in data["transactions"]:
                writer.writerow(
                    [transaction["occurred_on"], transaction["kind"], transaction["description"], transaction["category"] or "", transaction["amount_cents"]]
                )
        return path

    def xlsx(self, user_id: int, data: dict[str, Any]) -> Path:
        try:
            import xlsxwriter
        except ImportError as error:
            raise RuntimeError("Instale as dependencias com: pip install -r requirements.txt") from error
        path = self._path(user_id, data["start"], data["end"], "xlsx")
        workbook = xlsxwriter.Workbook(path)
        title = workbook.add_format({"bold": True, "bg_color": "#0F766E", "font_color": "#FFFFFF"})
        money = workbook.add_format({"num_format": 'R$ #,##0.00'})
        sheet = workbook.add_worksheet("Resumo")
        sheet.set_column("A:A", 28)
        sheet.set_column("B:B", 18)
        sheet.write("A1", "Indicador", title)
        sheet.write("B1", "Valor", title)
        summary = data["summary"]
        labels = {
            "gross_cents": "Faturamento bruto", "fees_cents": "Taxas", "net_cents": "Liquido das vendas",
            "income_cents": "Entradas", "expenses_cents": "Despesas", "profit_cents": "Resultado",
            "ticket_cents": "Ticket medio", "effective_fee_bps": "Taxa efetiva (bps)",
        }
        for index, (key, label) in enumerate(labels.items(), start=1):
            sheet.write(index, 0, label)
            if key == "effective_fee_bps":
                sheet.write(index, 1, summary[key] / 100)
            else:
                sheet.write_number(index, 1, summary[key] / 100, money)
        sales_sheet = workbook.add_worksheet("Vendas")
        columns = ["ID", "Data", "Item", "Cliente", "Pagamento", "Bruto", "Desconto", "Taxa", "Liquido", "Parcelas", "Status"]
        for index, label in enumerate(columns):
            sales_sheet.write(0, index, label, title)
        for row_number, sale in enumerate(data["sales"], start=1):
            values = [
                sale["id"], sale["occurred_on"], sale["item_description"] or sale["product_name"] or "Venda",
                sale["client_name"] or "", sale["payment_method"], sale["gross_cents"] / 100,
                sale["discount_cents"] / 100, sale["fee_cents"] / 100, sale["net_cents"] / 100,
                sale["installments"], sale["status"],
            ]
            for column, value in enumerate(values):
                sales_sheet.write(row_number, column, value, money if 5 <= column <= 8 else None)
        sales_sheet.autofilter(0, 0, max(1, len(data["sales"])), len(columns) - 1)
        sales_sheet.set_column("A:K", 16)
        transaction_sheet = workbook.add_worksheet("Lancamentos")
        for index, label in enumerate(["Data", "Tipo", "Descricao", "Categoria", "Valor"]):
            transaction_sheet.write(0, index, label, title)
        for row_number, transaction in enumerate(data["transactions"], start=1):
            transaction_sheet.write_row(
                row_number,
                0,
                [
                    transaction["occurred_on"], transaction["kind"], transaction["description"],
                    transaction["category"] or "", transaction["amount_cents"] / 100,
                ],
            )
            transaction_sheet.write_number(row_number, 4, transaction["amount_cents"] / 100, money)
        transaction_sheet.set_column("A:E", 20)
        workbook.close()
        return path

    def pdf(self, user_id: int, data: dict[str, Any]) -> Path:
        try:
            from reportlab.lib import colors
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import getSampleStyleSheet
            from reportlab.lib.units import cm
            from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
        except ImportError as error:
            raise RuntimeError("Instale as dependencias com: pip install -r requirements.txt") from error
        path = self._path(user_id, data["start"], data["end"], "pdf")
        document = SimpleDocTemplate(str(path), pagesize=A4, rightMargin=1.5 * cm, leftMargin=1.5 * cm, topMargin=1.5 * cm)
        styles = getSampleStyleSheet()
        story = [Paragraph("Meu Financeiro - Relatorio", styles["Title"])]
        story.append(Paragraph(f"Periodo: {human_date(data['start'])} a {human_date(data['end'])}", styles["Normal"]))
        story.append(Spacer(1, 0.4 * cm))
        summary = data["summary"]
        overview = [
            ["Faturamento bruto", brl(summary["gross_cents"])],
            ["Taxas", brl(summary["fees_cents"])],
            ["Liquido", brl(summary["net_cents"])],
            ["Entradas", brl(summary["income_cents"])],
            ["Despesas", brl(summary["expenses_cents"])],
            ["Resultado", brl(summary["profit_cents"])],
            ["Ticket medio", brl(summary["ticket_cents"])],
        ]
        summary_table = Table([["Indicador", "Valor"], *overview], colWidths=[9 * cm, 6 * cm])
        summary_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F766E")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (1, 1), (1, -1), "RIGHT"),
            ("PADDING", (0, 0), (-1, -1), 6),
        ]))
        story.extend([summary_table, Spacer(1, 0.5 * cm), Paragraph("Ultimas vendas", styles["Heading2"])])
        sales_rows = [["#", "Data", "Item", "Pagamento", "Liquido", "Status"]]
        for sale in data["sales"][:20]:
            sales_rows.append([
                str(sale["id"]), human_date(sale["occurred_on"]), (sale["item_description"] or sale["product_name"] or "Venda")[:28],
                sale["payment_method"], brl(sale["net_cents"]), sale["status"],
            ])
        if len(sales_rows) == 1:
            sales_rows.append(["-", "-", "Sem vendas", "-", "-", "-"])
        sales_table = Table(sales_rows, colWidths=[0.8 * cm, 2 * cm, 5.8 * cm, 2.3 * cm, 2.6 * cm, 2.5 * cm])
        sales_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F766E")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("PADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(sales_table)
        document.build(story)
        return path

    def receipt(self, user_id: int, sale: Any, installments: list[Any]) -> Path:
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import getSampleStyleSheet
            from reportlab.lib.units import cm
            from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
            from reportlab.lib import colors
        except ImportError as error:
            raise RuntimeError("Instale as dependencias com: pip install -r requirements.txt") from error
        folder = self.reports_path / str(user_id)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"recibo_venda_{sale['id']}.pdf"
        document = SimpleDocTemplate(str(path), pagesize=A4, rightMargin=2 * cm, leftMargin=2 * cm, topMargin=2 * cm)
        styles = getSampleStyleSheet()
        story = [Paragraph("RECIBO DE VENDA", styles["Title"]), Spacer(1, 0.4 * cm)]
        item = sale["item_description"] or sale["product_name"] or "Venda"
        rows = [
            ["Venda", f"#{sale['id']}"], ["Data", human_date(sale["occurred_on"])], ["Item", item],
            ["Cliente", sale["client_name"] or "Nao informado"], ["Pagamento", sale["payment_method"].title()],
            ["Valor bruto", brl(sale["gross_cents"])], ["Desconto", brl(sale["discount_cents"])],
            ["Taxas", brl(sale["fee_cents"])], ["Valor liquido", brl(sale["net_cents"])], ["Status", sale["status"]],
        ]
        if sale["fiscal_document"]:
            rows.append(["Nota fiscal", sale["fiscal_document"]])
        table = Table(rows, colWidths=[5 * cm, 10 * cm])
        table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#E6FFFA")),
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("PADDING", (0, 0), (-1, -1), 7),
        ]))
        story.extend([table, Spacer(1, 0.5 * cm), Paragraph("Parcelas e recebimentos", styles["Heading2"])])
        parcel_rows = [["Parcela", "Previsto", "Valor", "Status"]]
        parcel_rows.extend([[str(row["number"]), human_date(row["expected_on"]), brl(row["amount_cents"]), row["status"]] for row in installments])
        parcels = Table(parcel_rows, colWidths=[3 * cm, 4 * cm, 4 * cm, 4 * cm])
        parcels.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F766E")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("PADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(parcels)
        document.build(story)
        return path
