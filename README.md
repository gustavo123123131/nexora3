# Lixo CPA — servidor localhost

Este repositório contém os módulos originais em Python com uma camada web que reproduz os controles CustomTkinter no navegador. A interface e a lógica permanecem nos módulos originais; a camada `webui/` apresenta os controles pela API local FastAPI.

## Abrir no navegador **sem baixar arquivos** (GitHub Codespaces)

1. Entre na sua conta do GitHub e abra **[Criar Codespace do nexora3](https://github.com/codespaces/new?hide_repo_select=true&ref=main&repo=1419118374)**. Alternativamente, na página deste repositório, clique em **Code → Codespaces → Create codespace on main**.
2. O Codespaces instala automaticamente Python, as dependências e o Chromium no servidor remoto, **não no seu PC**, e inicia a aplicação.
3. Abra a aba **PORTS / PORTAS** no editor web. Na porta **8765 — Lixo CPA - Painel Web**, clique em **Open in Browser / Abrir no navegador**. Também pode aparecer uma notificação automática para abrir o site.
4. Para visualizar e interagir com as janelas do Chromium que a automação abre no servidor, use a porta privada **6080** e acrescente `/vnc.html?autoconnect=true` ao endereço HTTPS dela (opcional).

**Importante:** o endereço gerado tem o formato `https://NOME-DO-CODESPACE-8765.app.github.dev`, **não** `localhost` do seu PC. Os processos e as automações rodam no Codespace. Mantenha ambas as portas com visibilidade **Private / Privada**, porque o painel não tem login próprio. O Codespace pode parar quando fica inativo e a utilização pode consumir cotas ou gerar custos do GitHub. Os perfis/sessões antigos (que não foram publicados por segurança) não são automaticamente migrados para a nuvem. Integrações externas e automações completas precisam ser verificadas no ambiente remoto.

O modo localhost tradicional continua disponível abaixo.

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
