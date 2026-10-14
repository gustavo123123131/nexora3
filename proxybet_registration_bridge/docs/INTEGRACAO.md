# Checklist técnico para outra IA integrar ao projeto principal

**Não reescrever a aplicação existente.** Antes de modificar arquivos do outro repositório, identificar o sistema de perfil Chrome, IPC/HTTP backend e persistência de contas. Este pacote é uma base funcional em ambiente simulado, não uma integração real com sites de aposta.

## 1. Encontrar os serviços existentes

Buscar por abertura de perfis de navegador, `userDataDir`, `BrowserWindow`, `webContents`, `ipcMain.handle`, `playwright`, `puppeteer`, `chrome-remote-interface` ou serviço HTTP `localhost`. Confirmar se o navegador é Electron, Chrome instalado ou Chromium embutido. Não criar motor de navegador novo se o projeto já tiver.

## 2. Mapear o adapter de navegador

Implementar os métodos definidos em `src/browser_adapter.js`:

- `openProfile({profileId,url})`: receber o perfil **já existente** (sem substituir dados nem copiar cookies), retornar um handle local seguro.
- `navigate({handle,url})`: visitar a URL validada para esse site, sem redirecionamento arbitrário.
- `inspectFields({handle,selectors})`: retornar mapa booleano por campo. Esperar carregamento e DOM dinâmico se necessário, com timeout.
- `fillFields({handle,selectors,values})`: somente campos declarados, no documento correto. Jamais gravar dados de preenchimento em log.
- `submitForm({handle,selector})`: enviar só após confirmação explícita recebida pelo controlador de cadastro.
- `waitForRegistrationOutcome({handle,siteId})`: `completed` APENAS se houver sinal de êxito inequívoco do provedor; `pending` para SMS/e-mail/verificação/resultado incerto; `failed` se a plataforma reportar rejeição.

Configurar um **tempo limite** no adapter real; um polling sem limite travará a interface. Não usar truques de fingerprint, webdriver ou captchas.

## 3. Configurar plataforma real

Edite `examples/site_config.example.json` ou um registro equivalente privado. A amostra vem com `authorized:false`, `example.com` e seletores **fictícios**. Não alterar para `true` sem autorização da plataforma e verificação dos seletores reais. A UI deve permitir configurar campos por site; não pressupor que todos compartilham a mesma tela.

## 4. Mapear botões da UI

- **Abrir Conta** → `prepare`, mostrar campos detectados e informações do site.
- **Revisar e Confirmar** → `submit` (`userConfirmed:true` somente após interação real, nunca automaticamente).
- **Verificar pendência** → `refresh`.
- **Contas** → `store.list` (filtros `query` e `status`).
- **Abrir conta existente** → `launcher.open(id)`.

## 5. IPC ou localhost

`examples/electron_ipc.example.js` mostra canais locais, com gancho obrigatório `checkAuthorizedSender(event)`. Para aplicativo que usa HTTP localhost, instale rotas protegidas pelo mesmo mecanismo de sessão do projeto; **nunca** expor API de cadastro em `0.0.0.0` sem controle de acesso ou aceitar requisições cross-origin irrestritas. Confirme `Origin`, limite de tamanho, autenticação e proteção CSRF conforme arquitetura.

## 6. Credenciais

O armazenamento só guarda id, site, perfil, loginIdentifier, status, estágio e observações sanitizadas. A senha só é recebida na chamada de envio; se quiser login pré-preenchido depois, injete um `credentialProvider` vinculado ao cofre protegido do Windows. O launcher **não** faz submit de login nem ignora MFA.

## 7. API direta

Se uma plataforma liberar API formal e fornecer contrato, configure `officialApi` com `authorized:true`, `baseUrl` e lista de operações, autenticando com `tokenProvider` **apenas no backend local**. O `AuthorizedApiClient` valida origem e permite somente as operações listadas; ele não se conecta à ProxyBet por padrão. Implementar conversão de respostas de cada operação segundo documentação real. Não construir payloads assumidos com base em capturas de rede.

## 8. Teste de aceitação

1. Criar configuração autorizada de teste; clicar Abrir Conta abre o perfil correto.
2. Cadastro não executado sem confirmação explícita.
3. Campos faltando ou inválidos bloqueiam envio.
4. SMS/MFA pendente não aparece como cadastro concluído.
5. Cadastro confirmado pelo provedor cria status completed.
6. Reabrir conta usa o mesmo perfil, sem exportar cookies.
7. Logs não contêm senha, CPF, token ou resposta HTTP bruta.
8. Reiniciar backend preserva registros, não senhas ou handles.
9. `npm test` sem rede passa integralmente.

## Arquivos que **devem** mudar no projeto principal (somente depois de inspecioná-lo)

- Componente da UI que contém botão **Abrir Conta** e seção **Contas**.
- Controlador de navegador existente, via camada adaptadora.
- Backend local / Electron main para registrar novos comandos.
- Configuração de sites e destino de dados no userData do app.

**Não existe como nomear precisamente esses arquivos sem inspecionar a versão atual do projeto principal.**
