# Como contribuir

Obrigado por contribuir com o Meu Financeiro Telegram.

## Ambiente

1. Faça um fork e crie uma branch descritiva, por exemplo `feat/alerta-das`.
2. Crie o ambiente virtual e instale as dependências conforme o README.
3. Configure um token de teste em `.env`; não publique esse arquivo.

## Antes de abrir um pull request

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests -v
```

- Mantenha as alterações focadas e documente novos comandos no README.
- Inclua testes para mudanças em cálculos, status ou persistência financeira.
- Não adicione dados reais, tokens, banco SQLite, comprovantes ou relatórios.
- Use mensagens de commit curtas no imperativo, como `feat: adiciona alerta de DAS`.

## Pull requests

Explique o problema resolvido, o comportamento esperado e como foi validado. Alterações que modifiquem cálculos financeiros, dados ou permissões de acesso precisam de revisão cuidadosa.
