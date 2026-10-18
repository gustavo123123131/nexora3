# Nexora — Browser Workspace

Aplicação local para abrir e controlar múltiplos navegadores Chromium, com
perfis persistentes separados, interface no navegador e gerenciador de proxy.

## Recursos ativos

- Duas abas de abertura: **Mãe** e **Filha**, com links, quantidade de janelas,
  modo desktop/Android/iPhone, pausa entre aberturas e logs.
- Abertura de navegadores pela interface Mãe e Filha, com perfis e
  dispositivos individuais quando a operação utiliza uma conta salva.
- Multi-login: cada instância Chromium usa sua própria pasta de perfil;
  diretórios e sessões existentes são preservados.
- Aba **Proxy**: salvar, listar, editar, remover, testar conectividade TCP,
  ativar e desativar proxies HTTP/HTTPS/SOCKS5 no agente Windows.
- Pareamento entre painel privado no Codespaces e agente Windows, com
  proteção CSRF e chave de agente.

## Contas e operações na versão atual

- A aba **Contas** apresenta perfis individuais, detalhes de identificação,
  associação de dispositivo, abertura de sessão e opções de exclusão/backup.
- A aba **Chave Pix** mantém o cadastro local de chaves. A seção
  **Operações** possui tentativa de vinculação pela sessão Chromium da
  conta selecionada; o resultado precisa ser confirmado pela plataforma.
- A seção **Verificar saque** consulta os pré-requisitos conhecidos e mantém
  histórico do diagnóstico. **Não realiza envio de pedido de saque** nesta
  versão; nenhuma ação de `cashV3` está implementada no botão.
- Existem módulos de preparação/preenchimento e observação de cadastro no
  código, portanto esta versão não deve ser descrita como um navegador sem
  recursos de contas. Um clique em Registrar, isoladamente, não comprova a
  conclusão do cadastro remoto.

**Preservação dos dados:** perfis Chromium, sessões, proxies e backups
existentes não são alterados por esta revisão documental. Faça backup antes
de qualquer limpeza ou atualização manual.

## Instalação e uso

No Codespaces, nesta branch:

```bash
git switch fix/remover-criacao-contas
git pull --ff-only origin fix/remover-criacao-contas
bash .devcontainer/start.sh
```

### Windows — instalador único, atualizações automáticas

**Apenas uma última instalação:** execute
[`iniciar-agente-desenvolvimento.cmd`](iniciar-agente-desenvolvimento.cmd)
desta branch para instalar o supervisor na pasta
`%LOCALAPPDATA%\\LixoCPA-Agent`. Se já guardou esse CMD com o nome da
branch `fix/remover-criacao-contas`, pode usar o mesmo arquivo — ele
consulta o instalador atualizado no GitHub.

A instalação cria o **iniciador permanente**:

```text
%LOCALAPPDATA%\LixoCPA-Agent\iniciar-agente-local.cmd
```

Esse é o único iniciador que você precisará usar posteriormente. Enquanto
ele estiver aberto:

- `atualizador_nexora.py` verifica o GitHub a cada **3 minutos**
  exclusivamente na branch `fix/remover-criacao-contas`;
- Se houver versão nova, prepara os arquivos, mas **não interrompe janelas
  Chromium ativas**; espera que Mãe e Filha terminem;
- Quando as janelas estiverem fechadas, o supervisor troca o código, reinicia
  o processo Python no mesmo terminal e **reutiliza o pareamento**.
  O painel aguarda a reconexão e não deve exigir o código novamente
  durante uma reinicialização normal;
- Preserva `__profiles/`, cookies/sessões, `accounts.json`, configurações,
  `proxies.json`, o ambiente Python `.venv/` e os logs;
- Se o GitHub ficar indisponível ou um pacote for inválido, mantém a
  versão anterior e tenta novamente em outra verificação.

**Não precisa baixar nenhum novo CMD, fechar o terminal ou reinstalar a
cada commit.** O Windows precisa permanecer ligado, com o supervisor em
execução; se desligar o PC, abra o iniciador local uma vez após ligar.
Uma instalação inicial desse supervisor é inevitável porque agentes
baixados antes dele não conseguem se atualizar sozinhos.

O console mostrará o status de atualização. Se existirem janelas ainda
abertas, a atualização ficará pendente. O código é atualizado somente
a partir do repositório GitHub vinculado; não publique essa branch se
não quiser atualizar as máquinas ligadas a ela.

**Não use `iniciar-agente-windows.cmd`** para iniciar esta branch: ele
consulta `main` e não ativa o supervisor de desenvolvimento.

Para executar localmente:

```bash
python -m pip install -r requirements-web.txt
python -m playwright install chromium
python servidor.py
```

Acesse `http://127.0.0.1:8765`. O painel e o navegador Chromium são
processos distintos. Os navegadores abrem na máquina do agente Windows.

## Testes

```bash
python -m pytest -q tests/test_localhost.py tests/test_simple_open.py \
  tests/test_proxy_workspace.py tests/test_device_modes.py tests/test_visual_ui.py
```

Os testes usam páginas locais e não cadastram contas em plataformas externas.

## Segurança

Proxies e pastas Chromium podem conter credenciais e sessões privadas.
Não publique `proxies.json`, dados de perfis ou backups no GitHub.
O arquivo local `proxies.json` contém a senha do proxy em texto legível,
embora a API de listagem não a retorne.

A remoção do fluxo de cadastro foi isolada nesta branch para permitir
reversão sem afetar a branch anterior.
