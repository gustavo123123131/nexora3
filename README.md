# Lixo CPA — servidor localhost

Este repositório contém os módulos originais em Python com uma camada web que reproduz os controles CustomTkinter no navegador. A interface e a lógica permanecem nos módulos originais; a camada `webui/` apresenta os controles pela API local FastAPI.

## Modos de dispositivo e Área Pix

As abas **Criar Mãe** e **Criar Filha** permanecem; a aba **Área Pix** e os
controles exclusivos de Pix/saque estão ocultos por enquanto. Os módulos
compartilhados não foram apagados, para não interromper o multi-login.

No seletor **Modo**, estão disponíveis:

- **Mobile — Somente Android:** Chromium com viewport, toque e User-Agent Android.
- **Mobile — Somente iPhone:** Chromium com viewport, toque e User-Agent iPhone.
  É emulação de navegador móvel, **não Safari nativo no iOS**.
- **Mobile — Android e iPhone (alternados por instância):** instâncias ímpares
  usam Android e pares usam iPhone, com indicação no log. Essa ordem é
  determinística; para usar apenas um sistema, selecione o modo correspondente.
- **Aplicativo Android (indisponível):** a interface informa a limitação e não
  inicia um navegador fingindo ser um app nativo.
- **Computador:** Chromium desktop, com suporte ao caminho de perfis antigo.

Os perfis Android e iPhone novos ficam em
`Settings/Criar_Mae/__profiles/android|iphone/profile_N` (ou caminho
equivalente em `Criar_Filha`), separados entre si e do perfil desktop.
**Os perfis legados não foram apagados ou migrados automaticamente**, pois o
modo antigo "celular" não distinguia Android de iPhone; a migração teria risco
de misturar sessões. A opção **Manter perfis** continua a determinar se o
perfil será reutilizado ou recriado, como antes. As dimensões desktop
persistidas não sobrescrevem os novos perfis móveis.

Os testes de CI usam páginas locais de teste e contextos reais do Playwright
Chromium; nenhum cadastro, banco, saque ou API de terceiros é acionado.

## Painel no Codespaces + Chromium na area de trabalho do Windows

**Objetivo:** acessar a interface pelo endereco HTTPS do GitHub Codespaces (porta 8765)
e abrir/controlar as janelas do Chromium **no PC Windows**, nao no servidor remoto.

Como navegadores comuns nao podem iniciar programas Windows sozinhos, e **obrigatoria
uma pequena instalacao local inicial**. O instalador baixa o codigo-fonte original
e o Playwright/Chromium no computador; o repositorio nao recebe cookies, perfis ou
informacoes do navegador do PC.

1. Abra [GitHub Codespaces](https://github.com/codespaces). Se necessario, crie o
   Codespace do `nexora3`. Na aba **PORTS**, use o link HTTPS privado da
   **porta 8765** e mantenha essa aba aberta. O painel mostrara **Conectar Chromium do Windows**.
2. No **Windows**, baixe somente o pequeno arquivo
   [`iniciar-agente-windows.cmd`](iniciar-agente-windows.cmd)
   (no GitHub, clique em **Download raw file**) e execute-o. Ele
   baixa a copia necessaria do codigo do repositorio e instala o Chromium
   automaticamente. Precisa ter Python 3.11+ instalado; veja [python.org](https://www.python.org/downloads/windows/).
   Alternativamente, execute diretamente `instalar-agente-windows.ps1` no PowerShell.
3. Quando o agente perguntar, cole a URL **HTTPS da porta 8765** da aba **PORTS**
   (por exemplo `https://exemplo-8765.app.github.dev`).
   O terminal do Windows exibira um **codigo de pareamento temporario**.
4. Copie esse codigo no painel GitHub e clique **Conectar ao meu Windows**.
   Quando pressionar **Iniciar** na interface original, o Playwright abre janelas
   do Chromium na area de trabalho do Windows. Deixe a janela do agente aberta.
5. Depois da instalacao inicial, para iniciar novamente o agente sem reinstalar,
   use `%LOCALAPPDATA%\LixoCPA-Agent\iniciar-agente-local.cmd`.
   O agente pedira novamente o link atual da porta 8765.

**Seguranca:** o agente escuta exclusivamente em `127.0.0.1:8767`. Somente a
origem HTTPS do Codespace informada na inicializacao recebe CORS; comandos remotos
precisam de um codigo forte de pareamento e de token anti-CSRF. Nunca torne
a porta 8767 publica nem exponha o codigo de pareamento. Mantenha a
porta 8765 do Codespaces configurada como **Private**. O site remoto nao
executa automacoes no Codespaces quando o modo Windows esta ativo.

**Plano alternativo:** navegadores podem exigir permissao para um site HTTPS
se conectar ao `127.0.0.1`. Se a conexao do painel hospedado no Codespaces for
bloqueada pelo navegador, abra diretamente
**http://127.0.0.1:8767** no proprio PC (com o agente aberto).
Esta alternativa usa a **mesma interface e mesma logica original**, hospedada
localmente, e abre as janelas na area de trabalho.

O Codespaces pode consumir cotas ou custos e ser suspenso quando inativo. O
agente local nao requer o Codespaces ativo quando voce usa o endereco local
alternativo.

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
