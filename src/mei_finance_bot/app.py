from __future__ import annotations

import smtplib
import time
from email.message import EmailMessage
from pathlib import Path

from .config import Settings
from .database import FinanceRepository
from .reports import ReportExporter
from .telegram import FinanceBot, TelegramApiError, TelegramClient


class AccountantMailer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self.settings.smtp_host and self.settings.smtp_from)

    def send_report(self, recipient: str, path: Path) -> None:
        if not self.enabled:
            print(f"E-mail nao enviado a {recipient}: SMTP nao configurado.")
            return
        message = EmailMessage()
        message["Subject"] = "Relatorio automatico - Meu Financeiro"
        message["From"] = self.settings.smtp_from
        message["To"] = recipient
        message.set_content("Segue em anexo o relatorio financeiro automatico gerado pelo Meu Financeiro Telegram.")
        message.add_attachment(path.read_bytes(), maintype="application", subtype="pdf", filename=path.name)
        with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=30) as server:
            server.starttls()
            if self.settings.smtp_username and self.settings.smtp_password:
                server.login(self.settings.smtp_username, self.settings.smtp_password)
            server.send_message(message)


def main() -> None:
    settings = Settings.from_environment()
    repository = FinanceRepository(settings.database_path)
    telegram = TelegramClient(settings.token)
    bot = FinanceBot(
        telegram=telegram,
        repository=repository,
        exporter=ReportExporter(settings.reports_path),
        files_path=settings.files_path,
        annual_limit_cents=settings.mei_annual_limit_cents,
        allowed_user_ids=settings.allowed_telegram_ids,
    )
    mailer = AccountantMailer(settings)
    offset: int | None = None
    last_scheduled_check = 0.0
    print("Meu Financeiro Telegram iniciado. Pressione Ctrl+C para encerrar.")
    try:
        while True:
            try:
                for update in telegram.updates(offset):
                    offset = int(update["update_id"]) + 1
                    bot.handle_update(update)
                bot.send_pending_alerts()
                if time.monotonic() - last_scheduled_check > 600:
                    for recipient, path in bot.send_scheduled_reports():
                        try:
                            mailer.send_report(recipient, path)
                        except (OSError, smtplib.SMTPException) as error:
                            print(f"Falha ao enviar relatorio para contador: {error}")
                    last_scheduled_check = time.monotonic()
            except TelegramApiError as error:
                print(f"Telegram indisponivel: {error}. Nova tentativa em 5 segundos.")
                time.sleep(5)
    except KeyboardInterrupt:
        print("Bot encerrado.")
    finally:
        repository.close()


if __name__ == "__main__":
    main()
