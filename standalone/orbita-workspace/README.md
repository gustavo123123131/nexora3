# Órbita Workspace — Fase 1

Aplicativo **independente** de gerenciamento **local** de contas e perfis de navegador. Código novo, sem marcas, assets, endpoints ou módulos do ProxyBet. A implementação é deliberadamente limitada à Fase 1.

## Estrutura

- electron/main.ts — processo principal, janela protegida e inicialização
- electron/preload.ts — API IPC estritamente permitida
- electron/ipc.ts — validação de origem IPC e despacho de comandos
- electron/database.ts — SQLite, migrações, registros e auditoria
- electron/profiles.ts — janelas e partições persistentes independentes
- electron/validators.ts — validação de dados e URLs
- shared/contracts.ts — contratos tipados entre processos
- src/App.tsx — interface React com visão geral, contas mãe/filha, perfis e auditoria
- src/styles.css — tema próprio
- tests/ — testes de persistência, validação, isolamento e relacionamentos

## Pré-requisitos e instalação

**Node.js 22 LTS ou superior** (com npm) em Windows, macOS ou Linux.

Abra o terminal nesta pasta:

    npm install
    npm run dev

O Vite inicia a UI local em 127.0.0.1:5173 e o Electron abre a janela desktop.
Na aplicação, cadastre um perfil, informe uma URL sua ou oficial, associe uma
conta local e clique em **Abrir navegador**. Uma janela independente será
criada usando a partição persistente exclusiva daquele perfil.

Para compilar e iniciar em modo de produção:

    npm run build
    npm start

Testes:

    npm test
    npm run typecheck

Para gerar pacote distribuível, execute:

    npm run dist

O electron-builder gera os instaladores na pasta release/; o empacotamento
depende do sistema operacional e de suas permissões/ferramentas de assinatura.
A ausência de assinatura pode resultar em alerta do sistema operacional.

## Banco de dados e migrações

O arquivo SQLite fica em app.getPath('userData') / orbita.sqlite, no
armazenamento local do usuário, fora da pasta do projeto.

A versão 1 cria:
- browser_profiles(id, label, homeUrl, storagePartition, createdAt, updatedAt)
- accounts(id, provider, displayName, parentAccountId?, profileId?, status,
  createdAt, updatedAt), com foreign keys e ON DELETE SET NULL
- audit_events(id, actor, action, entityType, entityId, result, timestamp)
- schema_migrations(version, appliedAt)

A implementação utiliza SQLite via sql.js e salva o banco em arquivo binário
SQLite, com substituição por arquivo temporário a cada alteração, preservando
a portabilidade entre Windows, macOS e Linux. Cada mutação registra um evento,
sem serializar payloads contendo senhas, cookies ou tokens. Esta fase não
armazena nenhum segredo de provedor.

## Importação

Na aba Contas, selecione Importar para carregar CSV/JSON de registros próprios:
CSV deve incluir colunas provider,displayName; status (draft, ready, paused),
parentAccountId e profileId são opcionais. Um JSON pode conter um array de
objetos com esses campos. Senhas e tokens não são importados. Limite: 200
registros por importação. A importação de um lote é transacional.

## Modelo de segurança e limitações

- BrowserWindows de perfis utilizam contextIsolation, sandbox e nodeIntegration
  desabilitado, **sem preload** e sem acesso à API IPC da aplicação.
- Cada perfil usa uma partição diferente persist:orbita-UUID, isolando cookies,
  localStorage e sessões. A navegação é convencional e controlada pelo usuário.
- A janela de gestão possui contextIsolation, sandbox, preload allowlist e
  validação da origem dos comandos IPC.
- Para limpar um perfil, o usuário confirma a limpeza de cookies,
  armazenamento e cache. A remoção de perfil não garante apagamento seguro
  de blocos já gravados em disco; a limpeza de armazenamento usa APIs
  oficiais do Electron.
- Os relacionamentos de conta mãe/filha **são apenas locais**. Não são
  criadas contas nos provedores, não há CAPTCHA automático, engenharia de
  fingerprint, proxy, saldo, PIX, depósitos, saques ou transações.
- Não há integração com APIs externas. Não tente interpretar nomes de canais
  IPC do ProxyBet como endpoints HTTP.
- Sem autorização/documentação oficial não serão implementados adaptadores
  de provedores.

## Etapas futuras (fora do escopo da Fase 1)

Proxies/associação e verificação (Fase 2), PIX financeiro local e mocks de
operações com confirmação (Fase 3), adaptadores oficiais autorizados (Fase 4)
e endurecimento adicional/testes de recuperação e empacotamento (Fase 5).
