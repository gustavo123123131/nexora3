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
- A opção **Identificar plataforma e verificar conta** detecta a origem HTTPS
  salva no perfil, permite o redirecionamento exato com/sem `www` e faz
  consultas somente de leitura pela API `/hall/api` da própria sessão.
  A compatibilidade detectada não autoriza automaticamente envio de saque
  em todos os domínios: o executor `cashV3` segue com suporte de envio
  apenas à origem exata auditada nesta versão. Não são enviadas credenciais
  para sites diferentes da conta selecionada.
- O botão **Sacar** usa a sessão Chromium da conta selecionada e o PIN padrão
  `387520`, compartilhado com o cadastro Pix, sem exigir cadastro no cofre
  Windows. O servidor remoto ainda deve validar o PIN. A operação envia no
  máximo uma solicitação `cashV3` e consulta `orderInfo` para acompanhar o
  pedido; pedido aceito não significa pagamento concluído.
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


## Saque por API na sessão da conta (5GBet)

- Botão **Sacar — enviar pedido real** na seção Operações: consulta destino remoto único, saldo e taxa; usa o PIN de saque **já salvo no cofre do Windows**; envia uma única tentativa `cashV3` pela sessão Chromium correspondente; consulta `orderInfo` e registra número e estado observado.
- É necessário salvar o PIN em **Contas → Detalhes**, abrir e autenticar o Chromium da conta e confirmar que existe exatamente um destino Pix vinculado. O botão não cria PIN, contas, destinos nem novas sessões.
- Um pedido aceito não é sinônimo de pagamento concluído: a interface mantém o estado de acompanhamento até comprovação independente. **Atualizar estado do pedido** consulta novamente `orderInfo` sem enviar outro saque.
- Tentativas incertas, pendentes ou aceitas bloqueiam novo envio para a mesma conta, inclusive após reinicialização. Em caso de timeout, confira a plataforma antes de qualquer intervenção manual.
- **Limitação:** o script de referência demonstrou aceitação em uma sessão específica da `www.5gbet.com`; isso não comprova o contrato das demais casas. O envio é restrito a essa origem. Testes automatizados usam objetos simulados, não movimentam dinheiro.
