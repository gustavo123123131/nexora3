# Cadastro guiado e logs por perfil (Nexora3)

## O que está implementado

A aplicação continua usando o botão **Abrir Conta**, os perfis Chromium
persistentes e `accounts.json` que já existiam. Não cria outro navegador.

1. A aba Conta Mãe/Filha cria o perfil e seu registro.
2. O navegador abre a URL escolhida.
3. `registration_autofill.py` procura campos conhecidos do formulário
   (`account`, `userpass`, `confirmPassword`, `realName`, `email`, `phone`).
4. Em páginas locais de teste, pode preenchê-los com os dados de teste existentes.
5. Em plataformas reais, **dados fictícios não são enviados**. Na aba Contas,
   clique **Cadastrar com meus dados**, informe dados legítimos (não armazenados
   no JSON) e confirme expressamente o envio. O formulário pode exigir aceite
   de termos e confirmação via SMS/e-mail/CAPTCHA no site.
6. O backend `POST /api/registration/accounts/submit-existing` insere o comando
   numa fila em memória e o navegador **já aberto** executa o preenchimento
   e clique no botão oficial, no próprio thread Playwright. Não reproduz o
   body, tokens ou outras partes desconhecidas da API privada.
7. A observação de rede registra somente o status HTTP de
   `POST /hall/api/member/register`, quando presente. `HTTP 200`
   **não significa conta criada**.
8. A confirmação só é gravada quando aparece um seletor inequívoco
   fornecido pelo próprio operador para um provedor autorizado.

## Configurar evidência de confirmação

Antes de iniciar o servidor/agente, configure a variável de ambiente:

`NEXORA_REGISTRATION_SUCCESS_SELECTOR`

O valor deve ser um seletor CSS de um indicador real e inequívoco de
conta recém-registrada e autenticada, obtido no site com autorização.
**Não há seletor universal comprovado**. Sem essa configuração, o estado
permanece pendente/não confirmado mesmo quando o HTTP retorna 200.

## Logs por conta

Na aba **Contas -> Ver detalhes -> Histórico do cadastro**, aparecem
etapas com timestamps e, quando conhecido, o status HTTP:

- `test-data-blocked`: identidade fictícia não enviada à plataforma
- `review`: campos preenchidos, aguardando confirmação
- `submit-queued`: solicitação de envio aceita
- `review-real-data`: dados legítimos preenchidos no navegador
- `submitting`: clique no formulário realizado
- `provider-response-unverified`: servidor respondeu, sucesso não confirmado
- `provider-http-error`: resposta HTTP 4xx/5xx, sem deduzir erro definitivo
- `provider-confirmed`: indicador oficial de conclusão encontrado
- `submit-result-unknown` / `timeout-unverified`: conferir manualmente

Os logs são vinculados a um único perfil e contêm apenas códigos de
etapa, timestamps e códigos HTTP numéricos. Nunca incluem senhas, CPF,
cookies, tokens, bodies de requisição ou dados do formulário.

A senha real informada na aba Contas é mantida **somente na fila em
memória** enquanto aguarda o navegador; não é escrita em
`accounts.json`. Em reinícios, a tentativa deverá ser retomada
manualmente. A versão atual ainda guarda *credenciais fictícias geradas
para teste* no perfil local; não reutilize esse mecanismo para
persistir senhas de titulares reais.

## Arquivos

- `nexora_accounts/registration_autofill.py` — detecção e pré-preenchimento
- `nexora_accounts/registration_submit.py` — fila e monitoramento
- `nexora_accounts/service.py` — histórico persistido por perfil
- `Settings/Criar_Mae/gui_tab.py` e `Settings/Criar_Filha/gui_tab.py`
  — executam o comando dentro do navegador já aberto
- `servidor.py` — endpoint localhost protegido por CSRF
- `webui/static/accounts_view.js` e `.css` — interface e logs
- `tests/test_registration_autofill.py` e `tests/test_registration_submit.py`
  — testes offline, sem plataformas reais

## Executar testes

```bash
python -m pytest -q tests/test_registration_autofill.py tests/test_registration_submit.py tests/test_registration_bridge.py
```

A ação GitHub `Cadastro e logs — testes offline` executa estes testes
e valida sintaxe Python/JavaScript no Pull Request.

## Limitações

Não há integração HTTP direta autorizada/com contrato completo para
`/hall/api/member/register`. A automação usa exclusivamente o formulário
renderizado do provedor, quando autorizado. Não contorna CAPTCHA,
restrições de identidade, MFA, políticas antifraude ou limites do site;
não repete submissão após resultado incerto. A criação real só pode ser
atestada após a confirmação da plataforma e testes autorizados em
ambiente adequado.
