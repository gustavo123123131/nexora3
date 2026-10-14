# Ponte modular de cadastro e contas — integração localhost/Electron

**Versão 1.0.0 — 14/10/2026**

Esta entrega contém **código novo e independente**, desenhado a partir da análise de fluxos observados no ZIP da ProxyBet. Não inclui o código proprietário da ProxyBet, dumps, cookies, chaves, capturas de tráfego, perfis ou credenciais. **Não altera seu projeto principal.**

## O que está pronto (e o que não está)

| Função | Situação |
|---|---|
| URL HTTPS por padrão e bloqueio de protocolos perigosos | Implementado/testado |
| Configuração de campos por site, exigindo permissão explícita | Implementado/testado |
| Abrir formulário no perfil já existente | Interface implementada; exige ligar ao navegador do projeto principal |
| Identificar campos reais no DOM | Interface implementada; exige adaptar navegador existente |
| Validar usuário, e-mail, senha, CPF e telefone | Implementado/testado; validação de formato, não identidade |
| Gerar senha aleatória forte | Implementado/testado |
| Preencher e enviar somente após confirmação | Implementado/testado com navegador simulado |
| Verificação via SMS/e-mail/CAPTCHA | Não automatizada; status `pending`, ação do usuário |
| Salvar e pesquisar contas e status | Implementado/testado com JSON privado (processo local único) |
| Reabrir conta no perfil original | Implementado/testado em simulador; interface real pendente |
| Preenchimento de login por cofre externo opcional | Implementado por adaptador; sem salvar senha nem efetuar login automático |
| API oficial configurável e autorizada | Cliente HTTP implementado/testado com respostas simuladas |
| Integração direta e real à API ProxyBet/whitelabel | **Não implementada: falta documentação/autorização e contrato comprovado** |
| Sistema visual, botões, interface Contas | A integrar no projeto principal; não foram recriados |

## Evidências reais consultadas no ZIP

- `README_COLETA.md` registra 2 observações de cadastro da coleta de 24/08/2026. A sequência mostra uso do formulário e o frontend fazendo `POST /hall/api/member/register`; houve etapa de verificação de segurança via serviço do site. Isso **não identifica uma API pública suportada** nem demonstra que todas as plataformas tenham contrato idêntico.
- `proxybet_extension_snapshot_20260824/content.js`: funções `openRegisterForm`, `registerFill`, `registerResubmit`; comunicação entre extensão e página.
- `proxybet_extension_snapshot_20260824/main-world/80-form-fill.js`: mapeia campos `account`, `userpass`, `confirmPassword`, `realName`, `phone`, `cpf` e `email`, com preenchimento de campos presentes e segunda sondagem para campos dinâmicos.
- `proxybet_extension_snapshot_20260824/manifest.json`: Chromium Extension Manifest V3 e scripts em `document_start`.
- `asar_extracted_20260824/dist/app/preload.js`: canais `open-browser`, `contas:list`, `contas:save`, `contas:patch`, entre outros; são **IPC, não endpoints HTTP**.
- `asar_extracted_20260824/dist/app/main.js`: loader Electron com conteúdo protegido em `content.asar.enc` e `main.jsc`, dependência Bytenode. O motor completo não foi disponibilizado como JS fonte nesse arquivo.
- `asar_extracted_20260824/package.json`: ProxyBet 2.5.10; `@faker-js/faker`, `@msgpack/msgpack`, `bytenode`, `electron-updater`, `node-window-manager`, `qrcode`, `lz4-napi`, `unidecode`, `ws`; declara `"license": "MIT"`. Isso **não prova que todo código, dados ou conteúdo encapsulado possua licença MIT reutilizável**.

### API encontrada x API implementável

`POST /hall/api/member/register` e `/hall/api/member/geetest/validate/v4` aparecem em evidências da interação do **site** com seu backend. O payload completo, cabeçalhos, assinatura, permissão de terceiros e documentação oficial **não foram confirmados**. Não incluí chamadas a essas rotas. O `api_client` só chama operações explicitamente descritas em documentação e configuradas como autorizadas, usando credenciais providas pelo backend local.

## Arquivos a integrar

```text
proxybet_registration_bridge/
  package.json
  README.md
  src/
    index.js                 exportações
    registration.js          preparar → revisar → enviar → confirmar
    site_config.js           sites, URL e seletores autorizados
    validation.js            validação + senha aleatória
    account_store.js         registros locais com escrita atômica
    account_launcher.js      reabrir perfil e login (sem bypass)
    api_client.js            cliente de API oficial autorizada
    browser_adapter.js       contrato para conectar Chrome existente
    logging.js               logs com metadados permitidos
  examples/
    site_config.example.json configuração de demonstração DESATIVADA
    host_adapter.example.js  adaptador aos métodos do projeto existente
    electron_ipc.example.js  handlers exemplificativos do Electron
    localhost_controller.example.js exemplo de backend local
  test/
    registration.test.js     testes node:test, sem rede
  docs/
    INTEGRACAO.md
    ANALISE_ORIGEM.md
```

### Instalação e testes

Requer Node.js **20+** (testado com Node.js 22). **Nenhuma dependência NPM adicional**.

No seu projeto existente, copie somente a pasta `src/`, ou adicione o diretório inteiro como módulo local. No terminal dentro do pacote:

```powershell
npm test
```

Crie as instâncias no processo principal Electron ou backend local:

```js
const path = require('node:path');
const {
  AccountStore, createSiteRegistry, RegistrationService,
  AccountLauncher, createLogger
} = require('./src');
const { makeHostAdapter } = require('./examples/host_adapter.example');

const sites = createSiteRegistry([/* configurações reais AUTORIZADAS */]);
const store = new AccountStore(path.join(userDataDirectory,'contas.json'));
const browser = makeHostAdapter(existingBrowserService); // do seu app
const log = createLogger(entry => { /* gravar metadados, não corpos de requests */ });
const registration = new RegistrationService({sites,store,browser,log});
const launcher = new AccountLauncher({sites,store,browser,log});
```

**Não funciona automaticamente até que `existingBrowserService` seja mapeado para o serviço de Chromium já existente.** Não incluir senhas no JSON de contas, Redux, LocalStorage nem GitHub.

## Fluxo para a nova interface Contas

1. Usuário seleciona **site** e **perfil** e pressiona **Abrir Conta**.
2. Backend chama `registration.prepare({siteId,profileId,loginIdentifier:''})`.
3. Retorno contém `account.id`, `fields` presentes e `missingRequired`. Mostre ao usuário e aguarde revisão.
4. Usuário fornece campos reais exigidos pelo formulário. Valide. Ao pressionar **Confirmar cadastro**, chame `registration.submit({accountId,values,userConfirmed:true})`.
5. `pending` significa que houve envio ou verificação adicional, **não** cadastro concluído. `completed` exige confirmação comprovável da plataforma no adaptador; não use `HTTP 200` isolado.
6. `registration.refresh({accountId})` atualiza pendências enquanto a janela e processo permanecem vivos.
7. Área **Contas** chama `store.list({query,status})`; **Abrir** chama `launcher.open(accountId)` com o perfil existente.

O cadastro pendente persistido após reinício exige que o usuário volte ao site, pois handles de navegador não são serializados. Registros de status não significam sessão ativa.

## Segurança e limites

- Apenas sites explicitamente autorizados. Domínios e seletores devem ser configurados por plataforma; não inferir equivalência universal.
- Não tentar eliminar CAPTCHA, antifraude, KYC, MFA, SMS ou verificação de e-mail.
- Não copiar as partes proprietárias/criptografadas da ProxyBet, nem enviar dados sensíveis do ZIP à IA ou GitHub.
- `account_store` é JSON local com permissões restritas, **não criptografia de disco**. Ele não armazena senhas, tokens, CPF ou telefone. Em produção, se houver múltiplos processos escritores, substituir por SQLite transacional ou outro storage confiável e usar criptografia em repouso para identificadores sensíveis conforme a necessidade.
- `credentialProvider` é um ponto opcional para integrar Windows Credential Manager/DPAPI ou cofre existente. O exemplo não instala nem armazena credenciais automaticamente.
- Não expor `api_client` nem handlers IPC indiscriminadamente ao renderer; validar origem/remetente e autenticação local.
- Não existem credenciais ou endpoints específicos oficiais incluídos. A rota encontrada em rastros não é autorização.

## Integração em projeto não fornecido

As assinaturas exatas de `openExistingProfile`, `navigate`, `inspectFields`, `fillForm`, `clickSubmit` e `awaitVerifiedRegistration` são **nomes de exemplo**, não métodos descobertos no seu projeto. Abra `docs/INTEGRACAO.md`, identifique o serviço de navegador existente e adapte somente `examples/host_adapter.example.js` (ou implemente `src/browser_adapter.js` conforme o contrato). Preserve sua interface, perfis, proxies e sistema de localhost atuais.
