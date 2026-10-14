# Lixo CPA — servidor localhost

Este repositório contém os módulos originais em Python com uma camada web que reproduz os controles CustomTkinter no navegador. A interface e a lógica permanecem nos módulos originais; a camada `webui/` apresenta os controles pela API local FastAPI.

## Executar no Windows

Instale Python 3.11 ou mais recente (com tkinter) e execute **`iniciar-localhost.bat`**, ou:

```powershell
py -m pip install -r requirements-web.txt
py -m playwright install chromium
py servidor.py
```

Acesse **http://127.0.0.1:8765**. O servidor usa *live reload* / *hot reload* no desenvolvimento. `py servidor.py --no-reload` desativa a reinicialização automática.

## Segurança e dados locais

Este repositório é público. Arquivos de contas, cookies/sessões, perfis Chromium, segredos de extensões, configurações locais de proxy/bancos e chaves Pix **não devem ser versionados**. Eles foram excluídos intencionalmente e listados em `.gitignore`; o aplicativo cria ou carrega dados locais conforme sua configuração. Não copie arquivos sensíveis para o GitHub.

Não exponha a API à internet. A aplicação foi projetada para uso exclusivamente local e não possui autenticação multiusuário.

Leia [`README-LOCALHOST.md`](README-LOCALHOST.md) para detalhes, diferenças do desktop e limitações de testes.
