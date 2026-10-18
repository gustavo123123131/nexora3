# Nexora3 — projeto revisado e publicação

## Resultado entregue

O pacote `Nexora3_PROJETO_COMPLETO_REVISADO.zip` contém **todos os arquivos do ZIP original** e nove arquivos com modificações verificadas. Os arquivos originais do projeto continuam presentes; o pacote não contém modificações em APIs, URLs ou payloads.

Os nove arquivos alterados são:

- `webui/static/accounts_view.js` — textos de identificação de titular, CPF e salvamento.
- `webui/static/operations_view.js` — descreve a ação de saque como **verificação**, que é o que a versão atual executa; ajusta as mensagens da seção.
- `webui/static/operations_view.css` — atualiza comentários desatualizados.
- `nexora_accounts/pix_operations.py` — atualiza somente a documentação do módulo.
- `nexora_accounts/withdrawal_operations.py` — atualiza a documentação e adiciona um formatador de logs, sem tocar nos contratos com a plataforma.
- `nexora_accounts/session_launcher.py` — faz o log da verificação informar a causa concreta do bloqueio, quando disponível.
- `tests/test_withdrawal_safe_base.py` — acrescenta teste para esse diagnóstico, sem enviar operações reais.
- `README.md` — corrige a descrição desatualizada da aba Contas, Pix e da verificação.
- `README-LOCALHOST.md` — atualiza a descrição do painel e dos testes.

## O que NÃO foi implementado

**O botão de envio real `cashV3` não está implementado no ZIP.** A versão recebida realiza uma consulta prévia de saque por meio do endpoint interno `/api/operations/withdraw/preflight`. Nenhum teste executou envios para plataformas externas. Não chame a opção `Verificar saque` de botão de retirada real.

## Verificações executadas

- ZIP original íntegro, sem erros de CRC; 299 entradas.
- JavaScript: `node --check` nos dois arquivos modificados: OK.
- Sintaxe Python: `python -m compileall -q`: OK.
- Testes de regressão: **76 passaram**.
- Testes de conta, interface e diagnóstico: **15 passaram**.
- Um teste Playwright de navegador foi impedido por `ERR_BLOCKED_BY_ADMINISTRATOR` ao abrir `https://www.5gbet.com/` mesmo com respostas simuladas: **não foi validado neste ambiente**.
- Comparação automatizada: strings literais de rotas de API idênticas nos arquivos modificados; nenhuma função Python existente foi removida.

## Como publicar sem digitar comandos — GitHub Desktop

1. Instale/abra o **GitHub Desktop** e entre na conta do GitHub.
2. Vá em **File > Clone repository**, selecione `gustavo123123131/nexora3` e clone para uma pasta **nova** no seu computador.
3. Em **Current branch**, selecione `fix/remover-criacao-contas` e, em seguida, clique em **New branch**. Nome sugerido: `docs/linguagem-diagnostico-nexora`.
4. Extraia `Nexora3_SOMENTE_ARQUIVOS_ALTERADOS.zip` para uma pasta temporária.
5. Copie as pastas e arquivos extraídos para **dentro da pasta clonada**, escolhendo **substituir arquivos existentes**. As pastas (`webui`, `nexora_accounts`, `tests`) precisam manter a estrutura original.
6. Volte ao GitHub Desktop. Ele mostrará nove arquivos modificados. Confirme que são exatamente os nove listados neste relatório.
7. Digite a mensagem de commit `Revisar textos e diagnóstico do Nexora3` e clique em **Commit to docs/linguagem-diagnostico-nexora**.
8. Clique em **Publish branch** e depois em **Create Pull Request**. No GitHub, selecione como base `fix/remover-criacao-contas`.
9. Revise os diffs e aguarde os testes do GitHub. Só então decida se deseja fazer **Merge pull request**.

**Atenção:** o GitHub **não extrai automaticamente um ZIP** enviado como arquivo à página do repositório. A publicação precisa de cópia dos arquivos na estrutura existente e commit/PR, ou de uma ferramenta Git como o GitHub Desktop.

O Windows Agent acompanha a branch `fix/remover-criacao-contas`; uma PR isolada não atualiza o agente. Depois do merge, o atualizador poderá buscar as alterações conforme sua configuração.
