# Nexora3 — Diagnóstico do botão Abrir Conta e desenvolvimento sem PR

## Branch de trabalho

Todas as correções desta etapa ficam na branch EXISTENTE
`fix/contas-geracao-senha-fluxo`. A branch `main` não é atualizada,
e não foi criado Pull Request novo.

### Codespaces (terminal bash)

```bash
git fetch origin
git switch fix/contas-geracao-senha-fluxo || git switch --track origin/fix/contas-geracao-senha-fluxo
git pull --ff-only origin fix/contas-geracao-senha-fluxo
python .devcontainer/manage.py restart
bash .devcontainer/start.sh
```

O painel continua na porta privada **8765**. Após qualquer commit novo
na mesma branch, basta repetir `git pull --ff-only origin
fix/contas-geracao-senha-fluxo` e `python .devcontainer/manage.py restart`.
O supervisor com reload deve também captar automaticamente modificações locais.

### Agente Windows (também na branch de desenvolvimento)

O agente Windows NÃO compartilha o disco nem o código do Codespaces.
O arquivo normal `iniciar-agente-windows.cmd` sempre baixa `main`,
então **não o use para testar essa branch**.

Baixe UMA VEZ `iniciar-agente-desenvolvimento.cmd` da branch de
trabalho e execute-o quando quiser atualizar o agente. Ele busca a
versão atual da branch no GitHub e preserva perfis locais. Antes de
reexecutá-lo, feche o agente anterior para liberar a porta 8767.
O painel solicitará um novo código de pareamento.

O instalador original continua usando `main` por padrão; a opção
`-Branch` é específica para desenvolvimento.

## Limpeza segura de registros antigos

O arquivo de registros está em
`%LOCALAPPDATA%\Nexora\accounts.json` **no Windows**. O GitHub não
tem acesso a esses dados privados; nenhuma linha do código modifica
automaticamente o conteúdo existente após uma atualização.

Feche primeiro o agente Windows. Em PowerShell:

```powershell
Set-Location "$env:LOCALAPPDATA\LixoCPA-Agent"
.\.venv\Scripts\python.exe -m tools.limpar_perfis_teste
```

O primeiro comando apenas apresenta IDs de registros sintéticos, locais
e ainda não submetidos. NÃO apaga nada. Revise a prévia. Para confirmar:

```powershell
.\.venv\Scripts\python.exe -m tools.limpar_perfis_teste --apply
```

O sistema cria um BACKUP com nome `accounts.json.backup-...` antes
de eliminar registros identificados como testes sem submissão.
Não exclui perfis do Chromium, cookies, sessões, contas com resultado
incerto ou confirmado nem registros legados. O usuário deve revisar a
lista antes de confirmar.

## O que o fluxo faz

1. Clique no botão **Abrir Conta** na aba Mãe/Filha.
2. O armazenamento reserva a quantidade exata de perfis e impede
   a mesma solicitação duplicada dentro de 15 segundos.
3. Os dados sintéticos de cada perfil recebem nome legível,
   username único, senha forte e marcadores de teste.
4. O agente abre seu Chromium existente e salva etapas de início,
   carregamento da página e identificação do formulário.
5. Em demonstrações locais autorizadas, tenta preencher e enviar
   uma única vez pelo formulário. Captura HTTP e confirmação DOM.
6. Em sites reais, dados de identidade gerados artificialmente NÃO
   são enviados como identidade legítima. O sistema registra bloqueio
   e impede marcar a conta como concluída.
7. O perfil continua listado com o **estado correto**, mesmo que
   a abertura ou o formulário apresente erro.

## Onde olhar os logs

**Contas -> Ver detalhes -> Histórico do cadastro:** cada perfil
tem seu log persistente de até 100 eventos, incluindo horário UTC,
etapa, duração em milissegundos, código HTTP e categoria do erro.

**Terminal do agente Windows:** cada etapa aparece no formato:

```text
[2026-10-15T03:40:00+00:00] [perfil #1 id=abcd1234] [site-loading] 120 ms
[2026-10-15T03:40:01+00:00] [perfil #1 id=abcd1234] [site-loaded] 880 ms
```

O exemplo acima apenas demonstra o FORMATO, não é um log real.
Nenhuma senha, cookie, token, CPF completo, body HTTP ou URL com
parâmetros de autenticação deve ser registrada.

## Testes de ponta a ponta

O arquivo `tests/test_registration_e2e_local.py` inicia um servidor
HTTP em `127.0.0.1`, abre Chromium real e testa:
- abertura do formulário e preenchimento;
- um único clique/submissão e um único HTTP POST para a API simulada;
- confirmação inequívoca da página DEMO;
- persistência do status e histórico depois da reabertura;
- renderização e atualização da aba Contas em Chromium real, sem
  formulário manual, configuração de site ou opções extras.

`tests/test_account_flow_regression.py` verifica:
- proteção contra clique duplicado e chamadas simultâneas;
- persistência depois do reload;
- limpeza seletiva com backup, mantendo sessões e registros incertos;
- histórico com ID, horário e duração sem credenciais.

Os testes NÃO efetuam cadastro real em cassinos e NÃO provam o
contrato de API de uma plataforma específica.

## Dependência ainda não resolvida para cadastro real

Para confirmar um cadastro em sites externos, é necessário:
1. autorização do provedor para automação/cadastro;
2. identidade, contato e verificações pertencentes ao titular;
3. inspeção dos campos DOM na versão ATUAL da plataforma;
4. resultado oficial inequívoco do registro (não apenas HTTP 200);
5. cumprimento de CAPTCHA, KYC, limites e políticas do site.

O endpoint `/hall/api/member/register` foi observado no material
de pesquisa, mas o payload e autenticação oficiais para acesso
direto não foram comprovados. Não se deve fabricar esses contratos.

## Estado mais recente confirmado pelo usuário — 15/10/2026

No teste do agente Windows + Codespaces em uma plataforma externa,
o usuário confirmou que o sistema já preenche:
- `input[data-input-name="account"]` — username;
- `input[data-input-name="userpass"]` — senha do perfil;
- `input[data-input-name="confirmPassword"]` — confirmação da senha.

Relatou ainda que não preenchia o campo `realName`, não acionava o
`#insideRegisterSubmitClick` e **não conseguia visualizar os logs**.

O código desta branch recebeu os ajustes:
- Cada etapa de preenchimento agora emite log seguro no terminal Windows,
  na GUI e em `registrationLog` (sem salvar valores dos campos).
- A aba Contas apresenta logs **diretamente no cartão**, consulta mudanças
  a cada dois segundos e mantém `Ver detalhes` aberto no recarregamento.
- O navegador registra a presença/ausência do campo de nome e do botão
  Registro; se não pode enviar, registra `real-name-required` e
  `submit-not-sent` explicitamente.
- Em ambientes locais autorizados, o Chromium de teste preenche o nome
  sintético e aciona Registro. Em plataforma externa, **não preenche nome
  civil inventado nem envia cadastro fictício**; a titularidade não é
  comprovada por geradores de nomes e CPF.
- Senhas novas são geradas com nove caracteres (faixa pedida: 7–9).

**Ponto ainda não validado:** nenhum cadastro real concluído na plataforma
externa foi confirmado. Não tratar botão disponível, nome fictício,
HTTP 200 isolado ou abertura de Chromium como confirmação de conta.

## Nome real do titular — opção local para preencher realName

O log do usuário confirmou `real-name-field-found` (elemento encontrado),
mas o nome sintético de `profileData.fullName` não deve ser usado em
registros externos.

O agente Windows agora suporta a variável de ambiente **local**
`NEXORA_REGISTRATION_OWNER_NAME`. Quando definida com o nome legítimo
do titular, ela preenche:

```text
input[data-input-name="realName"]
```

O valor **não é gravado em `accounts.json` nem nos logs**. Eventos
persistidos são `real-name-filled`, `submit-button-found` e
`submit-not-sent`, com ID e duração. O sistema nunca substitui o
valor por um nome civil inventado se essa configuração estiver ausente.

Para configurar no Windows em um PowerShell de sua confiança,
**sem salvar o nome no repositório**, use:

```powershell
setx NEXORA_REGISTRATION_OWNER_NAME "NOME COMPLETO DO TITULAR"
```

Substitua pelo nome verdadeiro do titular e feche/reabra o terminal ou
programa para atualizar o ambiente. Execute novamente
`iniciar-agente-desenvolvimento.cmd`. Se não desejar persistir o nome no
ambiente do Windows, configure somente no processo que inicia o agente.

Mesmo com o nome preenchido, clicar em **Registro** em plataforma
externa depende das verificações, consentimento e política do serviço;
o sistema não envia cadastro usando CPF/e-mail/telefone sintéticos.
A aba Contas e o terminal registram `submit-not-sent` em vez de
declarar sucesso. Nos testes locais, o envio é automatizado usando
o botão real `#insideRegisterSubmitClick` de um formulário simulado.
