# Meu Financeiro Telegram

Bot financeiro para MEIs operado inteiramente pelo Telegram. Centraliza vendas, maquininhas, boletos, fluxo de caixa, impostos, alertas e relatórios, sem exigir painel web.

> O projeto controla informações financeiras, mas não realiza pagamento bancário nem emite NF-e. Essas ações exigem uma integração contratada com banco ou provedor fiscal.

## Funcionalidades

- Saldo consolidado por conta, valores a receber e previsão de caixa para 7, 15 e 30 dias.
- Cadastro de maquininhas, cálculo de taxas e antecipação de recebíveis.
- Vendas com cliente, produto, descontos, parcelas, baixa, estorno, nota fiscal e recibo em PDF.
- Boletos a pagar e receber, recorrência, status, linha digitável, leitura por foto e comprovantes anexados.
- Lançamentos de entrada e saída, contas, clientes e produtos.
- Alertas de boletos, DAS-MEI, limite anual do MEI, recebimentos, caixa negativo e taxas acima da contratada.
- Relatórios diário, mensal, anual e agendados, enviados pelo Telegram ou para o contador por e-mail.
- Exportações em PDF, XLSX e CSV.

## Requisitos

- Uma conta no Telegram e um bot criado com [@BotFather](https://t.me/BotFather).
- Docker + Docker Compose **ou** Python 3.11+ e Tesseract OCR.
- Para envio automático ao contador: credenciais SMTP.

## Início rápido com Docker

```bash
git clone https://github.com/SEU_USUARIO/meu-financeiro-telegram.git
cd meu-financeiro-telegram
cp .env.example .env
```

Edite `.env` e informe ao menos `TELEGRAM_BOT_TOKEN`. Depois inicie:

```bash
docker compose up --build -d
docker compose logs -f
```

Abra seu bot no Telegram e envie `/start`.

## Execução local

No macOS, instale o Tesseract com `brew install tesseract tesseract-lang`. No Ubuntu/Debian: `sudo apt-get install tesseract-ocr tesseract-ocr-por`.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
PYTHONPATH=src python -m mei_finance_bot
```

## Configuração

| Variável | Obrigatória | Descrição |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | Sim | Token criado no BotFather. Nunca o publique. |
| `ALLOWED_TELEGRAM_IDS` | Produção | IDs Telegram permitidos, separados por vírgula. |
| `DATABASE_PATH` | Não | Caminho do banco SQLite. Padrão: `data/mei_finance.db`. |
| `FILES_PATH` | Não | Diretório para documentos e comprovantes. |
| `REPORTS_PATH` | Não | Diretório para relatórios gerados. |
| `MEI_ANNUAL_LIMIT` | Não | Teto anual de faturamento usado pelos alertas. |
| `SMTP_HOST`, `SMTP_PORT` | E-mail | Servidor SMTP para relatórios do contador. |
| `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM` | E-mail | Credenciais e remetente SMTP. |

### Restrinja o acesso antes de usar em produção

1. Deixe `ALLOWED_TELEGRAM_IDS` vazio somente no primeiro acesso.
2. Envie `/inicio` ao bot e copie o ID exibido.
3. Atualize `.env`, por exemplo: `ALLOWED_TELEGRAM_IDS=123456789`.
4. Reinicie o container: `docker compose up -d`.

## Comandos do bot

| Área | Comandos |
| --- | --- |
| Início | `/inicio`, `/saldo`, `/ajuda` |
| Vendas | `/venda`, `/vendas`, `/venda_status ID recebida`, `/antecipar ID`, `/estornar ID`, `/cancelar_venda ID` |
| Venda detalhada | `/editar_venda ID descricao\|observacoes\|nota TEXTO`, `/nota ID NUMERO`, `/recibo ID` |
| Financeiro | `/lancamento`, `/conta`, `/contas`, `/boleto`, `/boletos`, `/baixar ID`, `/agendar_boleto ID` |
| Boletos | `/ler_boleto`, `/anexar boleto ID`, `/anexar venda ID` |
| Cadastros | `/maquininha`, `/maquininhas`, `/cliente`, `/clientes`, `/produto`, `/produtos` |
| Impostos | `/impostos`, `/limite VALOR` |
| Alertas | `/alertas`, `/alertar TIPO DIAS on\|off` |
| Contador | `/contador EMAIL`, `/agendar_relatorio PERIODO DESTINO on\|off` |
| Relatórios | `/relatorio diario\|mensal\|anual`, `/exportar pdf\|xlsx\|csv [INICIO] [FIM]` |
| Busca | `/buscar TERMO`, `/cancelar` |

Datas aceitam `DD/MM/AAAA` ou `AAAA-MM-DD`. Valores aceitam `1234,56` ou `1234.56`. Use `-` em campos opcionais de formulários guiados.

## Dados e backup

O volume `./data` contém o banco, anexos e relatórios. Faça backup periódico desse diretório com o bot parado ou usando um snapshot consistente do volume.

```bash
tar -czf backup-meu-financeiro-$(date +%F).tar.gz data/
```

Para restaurar, pare o container, substitua `data/` pelo conteúdo do backup e suba novamente. Não versionamos dados financeiros, anexos ou `.env`.

## Desenvolvimento

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests -v
```

O CI executa esses testes automaticamente a cada push e pull request. Consulte [CONTRIBUTING.md](CONTRIBUTING.md) para o fluxo de contribuição e [SECURITY.md](SECURITY.md) para reportar vulnerabilidades.

## Estrutura

```text
src/mei_finance_bot/
  app.py        ciclo de execução, agendamentos e SMTP
  config.py     configuração por variáveis de ambiente
  database.py   SQLite, persistência e consultas financeiras
  domain.py     validações e cálculos financeiros
  reports.py    geração de PDF, XLSX e CSV
  telegram.py   cliente Bot API e fluxos conversacionais
tests/          testes de regras financeiras
```

## Segurança

- Nunca envie `.env`, token, banco SQLite ou anexos para o GitHub.
- Ative `ALLOWED_TELEGRAM_IDS` antes de registrar dados reais.
- Use uma senha SMTP de aplicativo, não a senha principal da conta de e-mail.
- Restrinja acesso ao host e faça backups criptografados quando houver dados pessoais.

## Licença

Distribuído sob a licença MIT. Consulte [LICENSE](LICENSE).
