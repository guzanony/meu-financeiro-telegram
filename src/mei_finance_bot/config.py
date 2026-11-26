from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Carrega .env sem dependencia externa, preservando variaveis ja definidas."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    token: str
    database_path: Path
    files_path: Path
    reports_path: Path
    allowed_telegram_ids: frozenset[int]
    timezone: str
    mei_annual_limit_cents: int
    smtp_host: str | None
    smtp_port: int
    smtp_username: str | None
    smtp_password: str | None
    smtp_from: str | None

    @classmethod
    def from_environment(cls) -> "Settings":
        _load_dotenv(Path(".env"))
        raw_ids = os.getenv("ALLOWED_TELEGRAM_IDS", "").strip()
        allowed_ids = frozenset(int(item.strip()) for item in raw_ids.split(",") if item.strip())
        database_path = Path(os.getenv("DATABASE_PATH", "data/mei_finance.db"))
        files_path = Path(os.getenv("FILES_PATH", "data/files"))
        reports_path = Path(os.getenv("REPORTS_PATH", "data/reports"))
        for path in (database_path.parent, files_path, reports_path):
            path.mkdir(parents=True, exist_ok=True)
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token or token == "cole_o_token_do_bot_aqui":
            raise RuntimeError("Defina TELEGRAM_BOT_TOKEN no arquivo .env antes de iniciar o bot.")
        limit = os.getenv("MEI_ANNUAL_LIMIT", "81000.00").replace(",", ".")
        return cls(
            token=token,
            database_path=database_path,
            files_path=files_path,
            reports_path=reports_path,
            allowed_telegram_ids=allowed_ids,
            timezone=os.getenv("TIMEZONE", "America/Sao_Paulo"),
            mei_annual_limit_cents=int(float(limit) * 100),
            smtp_host=os.getenv("SMTP_HOST") or None,
            smtp_port=int(os.getenv("SMTP_PORT", "587")),
            smtp_username=os.getenv("SMTP_USERNAME") or None,
            smtp_password=os.getenv("SMTP_PASSWORD") or None,
            smtp_from=os.getenv("SMTP_FROM") or None,
        )
