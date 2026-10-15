# Lixo CPA — servidor localhost

## Aba Contas — apenas perfis locais

Nesta etapa, a aba **Contas** é um diretório dos perfis criados pelos botões
**Abrir Conta** nas abas Mãe e Filha. Não existem formulários de configuração
de sites, rotas de cadastro ou criação manual de contas nessa aba.

Ao clicar **Abrir Conta**, o agente Windows:
- respeita a quantidade por link já selecionada e cria uma pasta Chromium
  individual, sem reutilizar pastas existentes;
- grava um registro por perfil em `%LOCALAPPDATA%\\Nexora\\accounts.json`;
- gera nome de **teste**, username único de **6 a 9 caracteres alfanuméricos**, senha de **9 caracteres**
  (letras maiúsculas/minúsculas, números e símbolos), e-mail **não verificável**
  em `example.test`, telefone de teste e CPF com formato/dígitos verificadores
  válidos **não verificado nem associado a pessoa real**;
- abre Chromium nos links configurados. Em **demonstrações locais**, preenche
  e pode enviar o formulário de teste sem digitação. Em plataformas reais,
  **não** envia dados fictícios, pois nome civil e contato precisam ser legítimos;
- disponibiliza os registros para consulta na aba **Contas**, com CPF gerado
  visível e senha mascarada por padrão e botão **Ver detalhes**.

O JSON é gravado por substituição atômica. Contas antigas do arquivo
`accounts.sqlite` são importadas quando o `accounts.json` ainda não existe,
sem alterar ou apagar a base antiga. Registros antigos sem dados de teste
permanecem visíveis e seus campos indisponíveis são indicados como tal.

**Segurança:** os dados são gerados para teste; não os utilize
para representar identidades reais. CPF formatado corretamente **não**
comprova titularidade e pode coincidir com um CPF real. A senha aleatória fica armazenada no JSON local
**sem criptografia**, acessível ao usuário do Windows e ao agente pareado;
não publique esse arquivo no GitHub nem o compartilhe.

O botão **Abrir Conta** prepara o perfil e, em ambientes de demonstração
locais, tenta preencher e enviar o cadastro de teste automaticamente.
Em plataformas externas, dados de identificação fictícios continuam bloqueados:
username e senha podem ser gerados, mas nome e contatos legítimos devem ser
informados pelo titular. Nenhuma confirmação externa é inferida de um clique ou
HTTP 200. Os detalhes e os logs aparecem em **Contas**.

### Atualizar a instalação atual (máximo três ações)

1. No terminal do Codespaces na branch `main`, execute
   `git pull origin main`; o servidor com reload atualiza a interface.
2. No Windows, feche o agente antigo e execute novamente o mesmo arquivo
   `iniciar-agente-windows.cmd` já utilizado. Ele baixa o código do GitHub,
   mantém os perfis e inicia o agente atualizado.
3. Cole a URL HTTPS privada da porta 8765 no agente e use o novo código de
   pareamento no painel. Clique em **Contas** para acessar os módulos novos.

Para testes de integração do pacote de origem, no GitHub Actions executamos
`node --test proxybet_registration_bridge/test/registration.test.js`
e `pytest tests/test_registration_bridge.py`; nenhum deles registra contas
em plataformas reais.


Este repositório contém os módulos originais em Python com uma camada web que reproduz os controles CustomTkinter no navegador. A interface e a lógica permanecem nos módulos originais; a camada `webui/` apresenta os controles pela API local FastAPI.

## HTTPS automático nas abas Mãe e Filha

Ao clicar em **Abrir Conta**, endereços sem protocolo são normalizados
automaticamente: `exemplo.com` → `https://exemplo.com` e
`www.exemplo.com` → `https://www.exemplo.com`.
Os links com `https://` ou `http://` explícitos são preservados e
espaços nas extremidades são removidos. Para vários links separados por
vírgula, todos são normalizados e validados antes de iniciar os perfis.

URLs inválidas apresentam aviso nos logs e não iniciam a execução.
Em falhas de navegação HTTPS (incluindo falha de TLS), o sistema
registra uma mensagem clara e **não repete o ciclo de abertura** da
mesma URL nem tenta trocar automaticamente para HTTP. Não há
requisições antecipadas de validação: o erro de conexão é identificado
na navegação original do Playwright, preservando proxies e sessões.

## Interface essencial de abertura de contas

As abas **Criar Mãe** e **Criar Filha** mostram somente campos de links,
modo de dispositivo, quantidade por link, delay, **Abrir Conta**, controle
de parada da execução e logs. O botão **Abrir Conta** usa exatamente a
rotina já existente de abertura manual de perfis, sem automação de cadastro.

As opções avançadas (proxy por aba, automação, velocidade, jogos,
verificação de número, posicionamento das janelas, seleção de instâncias,
reutilização de dados e opções de perfis) estão **ocultas**, e os módulos
internos permanecem preservados. Isso evita a exclusão de dados antigos e
mantém compatibilidade com as configurações previamente salvas.

O gateway local de proxy existente (`Settings/local_proxy_gateway.py`)
permanece independente das duas abas. **Não foi criada uma área de proxy**:
uma interface compartilhada poderá ser acrescentada posteriormente, sem
misturar controles de proxy aos formulários de contas. Configurações antigas
de proxy ainda podem afetar a abertura, pois seus valores foram preservados
para não mudar a conectividade dos perfis existentes.

Os testes verificam os comandos **Abrir Conta** das duas abas usando um
worker simulado, sem cadastros ou sites externos, e validam a interface real
no Chromium.

## Workspace Proxy — interface visual (sem integração)

A navegação web agora possui **três áreas**: **Criar Mãe**, **Criar Filha**
e **Proxy**. A terceira área é renderizada apenas pelo JavaScript do
navegador (arquivos `webui/static/proxy_view.js` e
`webui/static/proxy_view.css`). **Não foi criada nenhuma rota nova ou
configuração de proxy no backend.** O gateway local e os perfis existentes
continuam intactos.

A tela Proxy contém nome, host, porta, protocolo (HTTP/HTTPS/SOCKS5),
usuário opcional, senha opcional com mostrar/ocultar, área de listagem
vazia, indicador **Integração pendente** e ações de salvar, editar e remover
**desabilitadas**. Todos os campos são demonstrativos: **não salvam nem
transmitem os dados**, não consultam APIs e não conectam/testam proxies.
Se a página for recarregada, o conteúdo digitado nessa tela desaparece.
A ligação entre os campos e uma futura API deverá ser implementada
explicitamente na próxima etapa, com validação e tratamento seguro de
credenciais.

A nova aparência grafite com acentos azul-violeta está concentrada em
`webui/static/workspace_theme.css`. Ela é aplicada tanto às telas de
contas quanto à navegação e à tela de pareamento, sem mudar os comandos
`Abrir Conta`, `Parar`, normalização HTTPS ou funcionamento localhost.
O teste `tests/test_visual_ui.py` confere as três telas em Chromium e
verifica que interagir com a visualização Proxy não dispara requisições
POST às APIs.

## Identidade visual Nexora

O painel web (localhost e GitHub Codespaces) usa a identidade **NEXORA —
Browser Workspace**: grafite escuro, acento azul-violeta, navegação lateral,
tipografia de interface desktop e cartões compactos para as configurações,
ações e logs. As abas **Criar Mãe** e **Criar Filha**, os controles de perfis,
modais, notificações e a tela de pareamento Windows utilizam o mesmo tema.
Em janelas estreitas, a navegação volta para o topo para manter os controles
acessíveis.

A mudança é **somente de apresentação**: arquivos de servidor, perfis,
proxies, persistência, scripts de automação e funções dos botões continuam
iguais. As classes visuais em `webui/static/app.js` classificam apenas os
botões/títulos existentes; não alteram callbacks ou comandos de rede.
As capturas de verificação visual são geradas pelos testes no GitHub Actions.

## Abrir ou recuperar o Codespaces (um comando)

Depois de abrir o [seu Codespaces](https://github.com/codespaces), no
**terminal do próprio Codespaces**, execute **apenas**:

```bash
bash .devcontainer/start.sh
```

Esse comando inicia o display auxiliar e o supervisor do FastAPI em ordem,
verifica o painel **8765**, não duplica processos já ativos e mostra a porta
a abrir. Ele também é usado pelo `postStartCommand` do devcontainer, quando
o GitHub inicia/reabre o ambiente. A configuração `forwardPorts` solicita
o encaminhamento automático das portas **8765** (painel) e **6080**
(Chromium remoto opcional). Se a porta não aparecer, abra a aba
**PORTS → Add Port → 8765**. Mantenha a visibilidade em **Private**.
O link é o endereço HTTPS da coluna **Forwarded Address**, normalmente
`https://NOME-DO-CODESPACE-8765.app.github.dev`; não é o localhost do Windows.

Para diagnóstico ou manutenção, no mesmo terminal:

```bash
python .devcontainer/manage.py status
tail -n 60 .runtime/lixocpa/server.log
tail -n 60 .runtime/lixocpa/supervisor.log
python .devcontainer/manage.py restart
python .devcontainer/manage.py stop
```

O comando principal `bash .devcontainer/start.sh` deve ser usado para
**iniciar ou recuperar** a aplicação; `restart` e `stop` são apenas para
manutenção intencional. O supervisor permite até **três falhas rápidas**
(< 60 segundos por tentativa), registra os motivos e interrompe as
tentativas se houver um erro persistente de configuração. Ele não encerra
processos alheios que estejam usando a porta. Caso já exista um servidor
iniciado manualmente, o comando não cria uma segunda cópia: acompanha
o processo existente sem interrompê-lo e inicia uma instância supervisionada
quando a porta ficar livre.

O reload automático do Uvicorn permanece habilitado para mudanças de
Python, HTML, CSS e JavaScript. O navegador detecta quedas do backend e
tenta reconectar enquanto a página está aberta; ao recuperar a conexão,
recarrega a interface. **Atualizar a página não inicia o servidor.**
A rota `/api/health` verifica apenas o servidor, sem rodar as automações.

### Limites do GitHub Codespaces

O Codespaces **não é hospedagem 24 horas por dia**. O GitHub documenta
desligamento por inatividade (30 minutos por padrão, configurável de
5 a 240 minutos) e limite máximo de 12 horas contínuas. Consulte
[as configurações de timeout](https://docs.github.com/en/codespaces/setting-your-user-preferences/setting-your-timeout-period-for-github-codespaces)
e [o ciclo de vida do Codespaces](https://docs.github.com/en/codespaces/about-codespaces/understanding-the-codespace-lifecycle).
Quando o Codespace está **parado**, nenhum supervisor interno funciona:
reabra o Codespace pelo GitHub e execute o comando único acima. Também
pode existir custo/cota de computação e armazenamento.

Para disponibilidade contínua, seria necessário hospedar o **painel**
em um serviço persistente (VM ou contêiner com reinicialização pelo
serviço de hospedagem, TLS, autenticação, monitoramento e armazenamento
durável privado). O **agente Windows/Chromium**, que abre os perfis no seu
computador, permanece local e precisa estar ligado e pareado;
uma hospedagem online não consegue abrir janelas num PC desligado.
Não exponha a porta 8767 nem perfis, cookies ou credenciais no GitHub.

### Exibição mobile em janelas do Windows

Em perfis Android e iPhone, o Chromium usa o **viewport CSS original**
(412 × 915 para Android; 390 × 844 para iPhone) e mantém a densidade
de pixels definida para o dispositivo. O ajuste de `Settings/mobile_window.py`
afeta apenas **o tamanho, a posição e a escala visual uniforme da janela**
quando o monitor é menor que a tela emulada; não altera cookies, perfil,
proxies, HTML/CSS dos sites visitados nem o modo desktop. O ajuste visual
não transforma um site mobile em desktop. Em monitores menores, a escala
de apresentação pode ser inferior a 100%, preservando o viewport CSS.

Se um site específico definir sua própria largura fixa ou fundo colorido,
essas características pertencem ao CSS do site; o projeto não injeta
estilos externos para forçar seu layout.

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
equivalente em `Criar_Filha`). O modo combinado usa pastas próprias
`__profiles/mixed/android/profile_N` e `__profiles/mixed/iphone/profile_N`,
sem compartilhar sessões com os modos individuais ou com o desktop.
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

**Saida do instalador Windows:** o arquivo `iniciar-agente-windows.cmd`
mostra apenas as etapas de instalacao, o **codigo de pareamento**, o
estado de inicializacao e avisos/erros. As mensagens detalhadas do pip e
do download do Chromium ficam em
`%LOCALAPPDATA%\\LixoCPA-Agent\\logs\\instalacao.log`.
O agente omite os logs repetitivos de requisições HTTP, mantendo avisos e
erros do servidor na janela. O codigo de pareamento **nao e gravado**
nesse arquivo de instalacao.

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
