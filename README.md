# Nexora — Browser Workspace

Aplicação local para abrir e controlar múltiplos navegadores Chromium, com
perfis persistentes separados, interface no navegador e gerenciador de proxy.

## Recursos ativos

- Duas abas de abertura: **Mãe** e **Filha**, com links, quantidade de janelas,
  modo desktop/Android/iPhone, pausa entre aberturas e logs.
- Botão **Abrir Navegadores**: abre os links sem gerar nomes, documentos,
  emails, usernames, senhas nem registros de contas.
- Multi-login: cada instância Chromium usa sua própria pasta de perfil;
  diretórios e sessões existentes são preservados.
- Aba **Proxy**: salvar, listar, editar, remover, testar conectividade TCP,
  ativar e desativar proxies HTTP/HTTPS/SOCKS5 no agente Windows.
- Pareamento entre painel privado no Codespaces e agente Windows, com
  proteção CSRF e chave de agente.

## Componentes de cadastro retirados

Foram removidos do fluxo e do código ativo:

- Geração automática de identidades, CPF, telefone, email, username e senha;
- Preenchimento automático de formulários de cadastro e botão Registro;
- Observadores do envio de cadastro e tentativas de confirmação;
- Antigo cadastro/login automático de contas salvas;
- Aba **Contas**, API de criação de contas e biblioteca de cadastro;
- O antigo pacote experimental de cadastro ProxyBet.

O projeto **não faz cadastro automático em sites**. A navegação normal nos
sites abertos permanece a cargo do usuário.

**Dados anteriores não são apagados:** se já houver `accounts.json`,
`contas/registros.json` ou pastas `__profiles` no Windows, esses arquivos
permanecem no disco. Eles não são mais produzidos ou consultados pelo novo
fluxo de abertura de navegadores. Faça backup antes de qualquer limpeza manual.

## Instalação e uso

No Codespaces, nesta branch:

```bash
git switch fix/remover-criacao-contas
git pull --ff-only origin fix/remover-criacao-contas
bash .devcontainer/start.sh
```

Para o agente Windows, reinicie usando
`iniciar-agente-desenvolvimento.cmd` após obter essa branch.
Não use `iniciar-agente-windows.cmd` para atualizar uma branch de
desenvolvimento: ele consulta `main`.

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
