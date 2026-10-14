# Inventário e rastreabilidade — arquivo de pesquisa ProxyBet

**Arquivo inspecionado:** `_proxybet_research.zip` (491 MB compactados, **8.647 entradas**, aprox. 1,39 GB não compactados). Foi lido o índice completo do ZIP e examinados conteúdos relevantes listados abaixo; **não** foi feito review manual linha a linha de todas as 8.647 entradas. Fonte histórica coletada em agosto de 2026, não teste atual da plataforma.

| Fonte (caminho sob `_proxybet_research/`) | Achado técnico | Status ||---|---|---|
| `README_COLETA.md` | Duas observações de cadastro e fluxo frontend + validação | Documento consultado |
| `proxybet_extension_snapshot_20260824/content.js` | `openRegisterForm`, `registerFill`, `registerResubmit` | Código consultado |
| `proxybet_extension_snapshot_20260824/main-world/80-form-fill.js` | Seletores `data-input-name` para sete campos; preenchimento e click | Código consultado |
| `proxybet_extension_snapshot_20260824/manifest.json` | Manifest V3, content scripts e permissões | JSON consultado |
| `asar_extracted_20260824/dist/app/preload.js` | API IPC de navegador/contas | Código consultado |
| `asar_extracted_20260824/dist/app/main.js` | Loader Electron, Bytenode, conteúdo protegido | Código consultado |
| `asar_extracted_20260824/package.json` | Versão 2.5.10, dependências e campo license | JSON consultado |
| `live_captcha/trace2/trace.json` | Linha do tempo HTTP relatada no README de coleta | Referenciada em evidências anteriores; não usada para extrair dados pessoais |

## Fluxo observado (não copiado)

1. App abre navegador com a plataforma.
2. Extensão encontra ação de cadastro ou formulário no DOM.
3. Interface recebe valores e os preenche em campos disponíveis.
4. Frontend do site envia a chamada HTTP de registro e solicita validação quando aplicável.
5. Extensão/app observam resposta/estado da página.

O novo pacote implementa esses passos por **interfaces próprias**, com confirmação explícita. Não inclui tentativas de manipular CAPTCHA, falsificar browser fingerprint ou contornar barreiras do provedor. Não reproduz componentes protegidos, licenças ou criptografia do ProxyBet.

## Sobre licenciamento

O `package.json` contém `license: MIT`, mas há dependências com licenças independentes e código encapsulado/compilado. Sem evidência de autorização sobre o conteúdo integral, este pacote é implementação nova, sem colar trechos da extensão, e não redistribui os artefatos originais. Consulte titular e licença efetiva antes de reutilizar fontes protegidas.

## APIs

- `POST /hall/api/member/register`: observado como operação do próprio **frontend** da plataforma; contrato HTTP e permissão de integração direta não demonstrados.
- `POST /hall/api/member/geetest/validate/v4`: pertencente ao fluxo de validação/segurança do site; não reproduzir nem automatizar bypass.
- `open-browser`, `contas:list`, `contas:save`, `contas:patch`: canais IPC do app desktop, **não URLs REST**.
- `AuthorizedApiClient` incluso é biblioteca genérica de operações oficiais allowlisted; não contém host, tokens nem rotas descobertas no tráfego da ProxyBet.
