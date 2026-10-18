/* Operations workspace: authenticated Pix API via selected account Chromium. */
'use strict';
(function () {
  // Future implementations live in separate modules; the catalog only
  // describes reserved interface slots and has NO execute handlers.
  const CATALOG = Object.freeze([
    Object.freeze({id: 'pix-bind', title: 'Cadastrar chave Pix',
      description: 'Vinculação de chave à conta selecionada.'}),
    Object.freeze({id: 'withdraw', title: 'Saque',
      description: 'Solicitação real por conta selecionada e acompanhamento do pedido.'}),
    Object.freeze({id: 'account-info', title: 'Informações da conta',
      description: 'Consulta de informações autorizadas da plataforma.'}),
    Object.freeze({id: 'future', title: 'Outras operações',
      description: 'Espaço reservado para funcionalidades futuras.'}),
  ]);

  const el = (tag, className, label) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (label != null) element.textContent = label;
    return element;
  };

  function create({get, post}) {
    const node = el('section', 'operations-local');
    const heading = el('header', 'operations-heading');
    const intro = el('div');
    intro.append(el('h1', '', 'Operações'),
      el('p', '', 'Selecione a conta e uma chave Pix. Os dados são lidos automaticamente da conta selecionada.'));
    const count = el('span', 'accounts-count', '0 contas');
    heading.append(intro, count);

    const selectorPanel = el('section', 'operations-panel');
    const selectorTitle = el('h2', '', 'Conta selecionada');
    const controls = el('div', 'operations-controls');
    const label = el('label', '', 'Selecionar conta');
    const selector = el('select', 'operations-account-select');
    selector.setAttribute('aria-label', 'Selecionar conta para operações');
    selector.append(new Option('Selecione uma conta', ''));
    label.append(selector);
    const refreshButton = el('button', 'accounts-details', 'Atualizar contas');
    refreshButton.type = 'button';
    controls.append(label, refreshButton);

    const feedback = el('p', 'operations-feedback');
    feedback.setAttribute('role', 'status');
    const details = el('div', 'operations-account-details');
    const hint = el('p', 'operations-hint',
      'Selecione uma conta cadastrada para visualizar os identificadores.');
    selectorPanel.append(selectorTitle, controls, feedback, details, hint);

    const sections = el('section', 'operations-catalog');
    sections.append(el('h2', '', 'Áreas de operações'),
      el('p', 'operations-hint',
        'Cadastro Pix, envio de saque e acompanhamento por conta, sem trocar de sessão.'));
    const cards = el('div', 'operations-grid');
    for (const item of CATALOG) {
      const card = el('article', 'operations-card');
      card.dataset.operation = item.id;
      card.append(el('span', 'operations-card-status', ['pix-bind', 'withdraw'].includes(item.id) ? 'Disponível' : 'Não disponível'),
        el('h3', '', item.title), el('p', '', item.description));
      cards.append(card);
    }
    sections.append(cards);

    const pixPanel = el('section', 'operations-panel operations-pix-panel');
    pixPanel.append(
      el('h2', '', 'Cadastrar chave Pix'),
      el('p', 'operations-hint',
        'Escolha uma chave salva. O Nexora3 configura automaticamente o PIN de saque padrão (387520) quando necessário, sem alterar a senha de login.')
    );
    const pixControls = el('div', 'operations-controls operations-pix-controls');
    const typeLabel = el('label', '', 'Tipo de chave Pix');
    const typeSelector = el('select', 'operations-pix-type');
    typeSelector.setAttribute('aria-label', 'Escolher tipo de chave Pix');
    for (const [value, title] of [
      ['', 'Selecione um tipo'], ['PHONE', 'Telefone'],
      ['EMAIL', 'E-mail'], ['CPF', 'CPF'], ['EVP', 'Aleatória (EVP)']
    ]) typeSelector.append(new Option(title, value));
    typeLabel.append(typeSelector);
    const keyLabel = el('label', '', 'Chave disponível');
    const keySelector = el('select', 'operations-pix-key');
    keySelector.setAttribute('aria-label', 'Escolher chave Pix cadastrada');
    keySelector.append(new Option('Escolha primeiro o tipo', ''));
    keyLabel.append(keySelector);
    pixControls.append(typeLabel, keyLabel);
    const pixFeedback = el('p', 'operations-pix-feedback');
    pixFeedback.setAttribute('role', 'status');
    const prepareButton = el('button', 'accounts-open operations-pix-prepare', 'Cadastrar');
    prepareButton.type = 'button';
    prepareButton.disabled = true;
    const preview = el('div', 'operations-pix-preview');
    const historyHeading = el('h3', 'operations-history-heading', 'Histórico local da conta');
    const history = el('div', 'operations-history');
    pixPanel.append(pixControls, preview, prepareButton,
      pixFeedback, historyHeading, history);
    const withdrawPanel = el('section', 'operations-panel operations-withdraw-panel');
    withdrawPanel.append(
      el('h2', '', 'Verificar saque'),
      el('p', 'operations-hint',
        'Consulta o destino, os limites, os dados de saldo e a taxa da conta selecionada quando disponíveis. Esta ação não envia pedido de saque.')
    );
    const checkWithdraw = el('button', 'accounts-details operations-withdraw-check',
      'Verificar conta de saque');
    checkWithdraw.type = 'button';
    checkWithdraw.disabled = true;
    const withdrawMessage = el('p', 'operations-withdraw-feedback');
    withdrawMessage.setAttribute('role', 'status');
    const withdrawHistory = el('div', 'operations-history');
    withdrawPanel.append(checkWithdraw, withdrawMessage, withdrawHistory);
    const submitWithdraw = el('button', 'accounts-open operations-withdraw-submit',
      'Sacar — enviar pedido real');
    submitWithdraw.type = 'button';
    submitWithdraw.disabled = true;
    const submitHint = el('p', 'operations-hint',
      'Este botão faz uma solicitação real à plataforma usando o PIN já salvo nesta conta. ' +
      'O clique autoriza um único pedido; não há reenvio automático. ' +
      'Confira o destino e o saldo no site antes de usar.');
    const refreshOrders = el('button', 'accounts-details operations-withdraw-status',
      'Atualizar estado do pedido');
    refreshOrders.type = 'button';
    refreshOrders.disabled = true;
    const ordersHeading = el('h3', 'operations-history-heading', 'Pedidos enviados');
    const orders = el('div', 'operations-history');
    withdrawPanel.append(submitHint, submitWithdraw, refreshOrders, ordersHeading, orders);
    node.append(heading, selectorPanel, pixPanel, withdrawPanel, sections);

    let listRevision = 0;
    let detailRevision = 0;
    let selectedId = null;   // integer LocalProfileStore id, never remote UID
    let selectedUuid = null; // immutable internal accountId UUID
    let selectedAccount = null;
    let profiles = [];
    let pixKeys = [];
    let pixRevision = 0;
    let historyRevision = 0;
    let preparing = false;

    let checkingWithdraw = false;
    let withdrawalRevision = 0;
    function updateWithdrawButton() {
      checkWithdraw.disabled = checkingWithdraw || !selectedAccount ||
        typeof post !== 'function';
    }
    let sendingWithdraw = false;
    let ordersRevision = 0;
    async function loadOrders() {
      const rev = ++ordersRevision;
      const selected = selectedAccount;
      if (!selected) { orders.replaceChildren(); return; }
      try {
        const result = await get('/api/operations/withdraw/orders?profileId=' +
          encodeURIComponent(selected.profileId));
        if (rev !== ordersRevision || !selectedAccount ||
            selectedAccount.accountId !== selected.accountId) return;
        orders.replaceChildren();
        for (const op of (result.orders || [])) {
          const item = el('article', 'operations-history-item');
          item.append(el('strong', '', 'Pedido — ' + (op.status || '—')),
            el('span', '', 'Resultado: ' + (op.message || '—')),
            el('span', '', 'Número do pedido: ' + (op.orderNo || 'Ainda não informado')),
            el('span', '', 'Valor: ' + (op.amount == null ? '—' : String(op.amount))),
            el('span', '', 'Estado da plataforma: ' +
              (op.providerStatus == null ? 'Não confirmado' : String(op.providerStatus))));
          orders.append(item);
        }
        submitWithdraw.disabled = sendingWithdraw || !selectedAccount ||
          (result.orders || []).some(op =>
            ['queued', 'attempting', 'accepted', 'pending',
              'verification_required', 'indeterminate'].includes(op.status));
        refreshOrders.disabled = !selectedAccount || !(result.orders || []).some(op =>
          op.orderNo && ['accepted', 'pending', 'verification_required'].includes(op.status));
      } catch (_) {
        orders.replaceChildren(el('p', 'operations-hint',
          'Não foi possível consultar o histórico de pedidos.'));
        submitWithdraw.disabled = true;
        refreshOrders.disabled = true;
      }
    }
    refreshOrders.addEventListener('click', async () => {
      const selected = getSelectedAccount();
      if (!selected || refreshOrders.disabled) return;
      refreshOrders.disabled = true;
      try {
        await post('/api/operations/withdraw/refresh', {
          profileId: selected.profileId, accountId: selected.accountId
        });
      } catch (error) {
        withdrawMessage.textContent = 'Consulta não concluída: ' + error.message;
      } finally { await loadOrders(); }
    });
    submitWithdraw.addEventListener('click', async () => {
      const selected = getSelectedAccount();
      if (!selected || sendingWithdraw || submitWithdraw.disabled) return;
      sendingWithdraw = true;
      submitWithdraw.disabled = true;
      withdrawMessage.textContent = 'Solicitando saque real. Não repita o pedido...';
      try {
        const response = await post('/api/operations/withdraw/submit', {
          profileId: selected.profileId, accountId: selected.accountId
        });
        if (selectedAccount && selectedAccount.accountId === selected.accountId) {
          withdrawMessage.textContent = response.operation?.message || 'Pedido registrado.';
          await loadOrders();
        }
      } catch (error) {
        if (selectedAccount && selectedAccount.accountId === selected.accountId) {
          withdrawMessage.textContent = 'Não foi possível confirmar o resultado. ' +
            'Confira o histórico antes de tentar novamente: ' + error.message;
          await loadOrders();
        }
      } finally {
        sendingWithdraw = false;
        await loadOrders();
      }
    });
    async function loadWithdrawHistory() {
      const rev = ++withdrawalRevision;
      const selected = selectedAccount;
      if (!selected || typeof post !== 'function') {
        withdrawHistory.replaceChildren();
        return;
      }
      try {
        const result = await get('/api/operations/withdraw/history?profileId=' +
          encodeURIComponent(selected.profileId));
        if (rev !== withdrawalRevision || !selectedAccount ||
            selectedAccount.accountId !== selected.accountId) return;
        withdrawHistory.replaceChildren();
        for (const op of (result.operations || [])) {
          const entry = el('article', 'operations-history-item');
          entry.append(
            el('strong', '', 'Verificação de saque — ' + (op.status || '—')),
            el('span', '', 'Resultado: ' + (op.message || '—')),
            el('span', '', 'Etapa: ' + (op.stage || '—'))
          );
          if (Array.isArray(op.events) && op.events.length) {
            const trace = el('details', 'operations-pix-trace');
            const rows = el('div', 'operations-pix-trace-lines');
            const stages = {
              withdrawInfoV2: 'Destino e limites',
              'gameCenter/gold': 'Saldo de jogo',
              getWithdrawFee: 'Cotação da taxa',
              contract: 'Validação final'
            };
            const events = {
              request_start: 'Requisição iniciada',
              api_accepted: 'Resposta aceita',
              api_rejected: 'Resposta recusada',
              request_exception: 'Falha de comunicação',
              shape_checked: 'Formato inspecionado',
              result: 'Resultado da verificação'
            };
            for (const line of op.events) {
              const words = [
                stages[line.stage] || line.stage || 'API',
                events[line.event] || line.event || ''
              ];
              if (Number.isInteger(line.httpStatus)) words.push('HTTP ' + line.httpStatus);
              if (Number.isInteger(line.providerCode)) words.push('Código API ' + line.providerCode);
              if (Number.isInteger(line.elapsedMs)) words.push(line.elapsedMs + ' ms');
              rows.append(el('div', 'operations-pix-trace-line', words.filter(Boolean).join(' | ')));
            }
            trace.append(el('summary', '', 'Ver etapas (' + op.events.length + ')'), rows);
            entry.append(trace);
          }
          withdrawHistory.append(entry);
        }
      } catch (_) {
        if (rev === withdrawalRevision) {
          withdrawHistory.replaceChildren(el('p', 'operations-hint',
            'Não foi possível consultar o histórico de saques.'));
        }
      }
    }
    checkWithdraw.addEventListener('click', async () => {
      const selected = getSelectedAccount();
      if (!selected || checkingWithdraw || typeof post !== 'function') return;
      checkingWithdraw = true;
      updateWithdrawButton();
      withdrawMessage.textContent = 'Verificando requisitos da conta...';
      try {
        const response = await post('/api/operations/withdraw/preflight', {
          profileId: selected.profileId, accountId: selected.accountId
        });
        if (selectedAccount && selectedAccount.accountId === selected.accountId) {
          withdrawMessage.textContent = response.operation?.message || 'Consulta registrada.';
          await loadWithdrawHistory();
        }
      } catch (error) {
        if (selectedAccount && selectedAccount.accountId === selected.accountId) {
          withdrawMessage.textContent = 'Falha ao consultar saque: ' + error.message;
        }
      } finally {
        checkingWithdraw = false;
        updateWithdrawButton();
      }
    });

    function maskedKey(key) {
      if (!key) return '—';
      return {PHONE: '(**) *****-****', EMAIL: '***@***',
        CPF: '***.***.***-**', EVP: '********-****-****-****-************'}[key.type] || '••••';
    }

    function activeKey() {
      return pixKeys.find(k => k.id === keySelector.value &&
        k.type === typeSelector.value) || null;
    }

    function updatePixPreview() {
      const selected = selectedAccount;
      const key = activeKey();
      prepareButton.disabled = typeof post !== 'function' || preparing || !selected || !key;
      preview.replaceChildren(
        summaryRow('Conta', selected ? 'Conta ' + selected.profileId : 'Não selecionada'),
        summaryRow('Identidade', selected ? (
          selected.identityStatus === 'verified' ? 'Verificada' :
            selected.identityStatus === 'observed' ? 'Observada, não verificada' : 'Ausente'
        ) : '—'),
        summaryRow('Chave', key ? maskedKey(key) : 'Nenhuma selecionada'),
        summaryRow('Execução', 'API da plataforma na sessão desta conta')
      );
    }

    function renderKeys() {
      const kind = typeSelector.value;
      const choices = pixKeys.filter(k => k.type === kind);
      keySelector.replaceChildren(new Option(
        !kind ? 'Selecione primeiro o tipo' : choices.length
          ? 'Selecione a chave' : 'Nenhuma chave deste tipo encontrada', ''));
      for (const [index, key] of choices.entries()) {
        keySelector.append(new Option(
          (kind === 'PHONE' ? 'Telefone' : kind === 'EMAIL' ? 'E-mail' :
            kind === 'CPF' ? 'CPF' : 'Aleatória') + ' #' + (index + 1) +
            ' — ' + maskedKey(key), key.id));
      }
      if (choices.length === 1) keySelector.value = choices[0].id;
      if (kind && !choices.length) {
        pixFeedback.textContent = 'Nenhuma chave Pix deste tipo está salva. Use a aba Chave Pix.';
      }
      updatePixPreview();
    }

    async function loadPixKeys() {
      if (typeof post !== 'function') return;
      const revision = ++pixRevision;
      try {
        const data = await get('/api/pix-keys');
        if (revision !== pixRevision) return;
        pixKeys = Array.isArray(data.keys) ? data.keys.filter(k =>
          k && typeof k.id === 'string' && typeof k.type === 'string') : [];
        const previous = keySelector.value;
        renderKeys();
        if (pixKeys.some(k => k.id === previous && k.type === typeSelector.value)) {
          keySelector.value = previous;
          updatePixPreview();
        }
      } catch (error) {
        if (revision === pixRevision) {
          pixKeys = [];
          renderKeys();
          pixFeedback.textContent = 'Não foi possível ler as chaves Pix: ' + error.message;
        }
      }
    }

    function renderHistory(ops) {
      history.replaceChildren();
      if (!ops.length) {
        history.append(el('p', 'operations-hint', 'Nenhuma operação registrada nesta conta.'));
        return;
      }
      for (const [index, op] of ops.entries()) {
        const entry = el('article', 'operations-history-item');
        entry.append(
          el('strong', '', 'Chave Pix — ' + op.keyType),
          el('span', '', 'Operação: ' + op.operationId),
          el('span', '', 'Estado: ' + (op.message || op.status)),
          el('span', '', 'Etapa: ' + (op.stage || '—') +
            (Number.isInteger(op.providerCode) ? ' / código: ' + op.providerCode : '')),
          el('span', '', 'Registrado em: ' + op.createdAt),
          el('small', '', (op.missingRequirements || []).join(' ')),
        );
        const trace = Array.isArray(op.events) ? op.events : [];
        if (trace.length) {
          const detail = el('details', 'operations-pix-trace');
          detail.open = index === 0 && op.status !== 'confirmed';
          const summary = el('summary', '', 'Ver etapas e logs (' + trace.length + ')');
          const entries = el('div', 'operations-pix-trace-lines');
          const names = {
            'security/status': 'Estado de segurança',
            modifyWithdrawPass: 'Configuração inicial do PIN de saque',
            verifyWithdrawPass: 'Verificação do PIN configurado',
            'withdrawInfoV2-antes': 'Consulta inicial de saque',
            verifyWithdrawalPasswordV2: 'Verificação do PIN de saque',
            bindalipayV3: 'Envio do cadastro Pix',
            'withdrawInfoV2-depois': 'Conferência final'
          };
          for (const item of trace) {
            const stage = String(item.stage || 'evento');
            const chunks = stage.startsWith('api.') ? stage.slice(4).split('.') : [];
            const stepName = names[chunks[0]] || chunks[0] || stage;
            const eventName = {
              request_start: 'Requisição iniciada',
              api_accepted: 'API aceitou a etapa',
              api_rejected: 'API recusou a etapa',
              request_exception: 'Erro de comunicação/cliente HTTP',
              list_check: 'Listagem localizada (conteúdo ainda não confirmado)',
              key_matched: 'Chave localizada na listagem',
              list_growth_confirmed: 'Nova entrada na listagem após cadastro aceito',
              list_no_growth: 'Não houve aumento detectável da listagem',
              authorization_received: 'Autorização temporária recebida',
              authorization_missing: 'Autorização temporária ausente',
              two_step_required: 'Verificação adicional exigida',
              withdraw_pin_initialized: 'PIN inicial verificado pela API',
              result: 'Resultado da etapa'
            }[chunks[1]] || chunks[1] || '';
            const fields = [stepName, eventName];
            if (Number.isInteger(item.httpStatus)) fields.push('HTTP ' + item.httpStatus);
            if (Number.isInteger(item.providerCode)) fields.push('Código API ' + item.providerCode);
            if (Number.isInteger(item.elapsedMs)) fields.push(item.elapsedMs + ' ms');
            if (item.responseShape) fields.push('Formato: ' + item.responseShape);
            if (item.detail) fields.push(String(item.detail));
            entries.append(el('div', 'operations-pix-trace-line', fields.filter(Boolean).join(' | ')));
          }
          detail.append(summary, entries);
          entry.append(detail);
        }
        history.append(entry);
      }
    }

    async function loadHistory() {
      const current = ++historyRevision;
      const profile = selectedAccount && selectedAccount.profileId;
      if (!profile || typeof post !== 'function') {
        history.replaceChildren();
        return;
      }
      try {
        const data = await get('/api/operations/pix/history?profileId=' +
          encodeURIComponent(profile));
        if (current !== historyRevision ||
            !selectedAccount || selectedAccount.profileId !== profile) return;
        renderHistory(Array.isArray(data.operations) ? data.operations : []);
      } catch (error) {
        if (current === historyRevision) {
          history.replaceChildren(el('p', 'operations-hint',
            'Falha ao consultar histórico local: ' + error.message));
        }
      }
    }
    typeSelector.addEventListener('change', () => {
      pixFeedback.textContent = '';
      renderKeys();
    });
    keySelector.addEventListener('change', updatePixPreview);
    prepareButton.addEventListener('click', async () => {
      const selected = getSelectedAccount();
      const key = activeKey();
      if (!selected || !key || typeof post !== 'function' || preparing) return;
      preparing = true;
      updatePixPreview();
      pixFeedback.textContent = 'Cadastrando chave Pix pela sessão Chromium desta conta...';
      try {
        const request = {
          profileId: selected.profileId,
          accountId: selected.accountId,
          keyId: key.id,
          keyType: typeSelector.value
        };
        const result = await post('/api/operations/pix/bind', request);
        if (selectedAccount && selectedAccount.profileId === selected.profileId &&
            selectedAccount.accountId === selected.accountId) {
          const op = result.operation || {};
          const messages = {
            confirmed: 'Cadastro Pix confirmado na listagem da plataforma.',
            already_registered: 'A chave Pix já estava cadastrada.',
            accepted_unconfirmed: 'API aceitou o cadastro; listagem ainda não confirmou. Não repita agora.',
            uncertain: 'Resultado incerto: confira a plataforma antes de repetir.',
            failed: 'Falha no cadastro Pix. Veja a etapa e o código no histórico.',
            queued: 'Operação enviada à sessão Chromium. Aguardando resultado.'
          };
          pixFeedback.textContent = (result.reused ? 'Operação existente: ' : '') +
            (messages[op.status] || op.message || 'Resultado desconhecido.');
          await loadHistory();
          if (op.status === 'queued') {
            for (let i = 0; i < 12; i++) {
              await new Promise(resolve => setTimeout(resolve, 2500));
              if (!selectedAccount || selectedAccount.profileId !== selected.profileId ||
                  selectedAccount.accountId !== selected.accountId) break;
              const data = await get('/api/operations/pix/history?profileId=' +
                encodeURIComponent(selected.profileId));
              const updated = (data.operations || []).find(x => x.operationId === op.operationId);
              if (!updated) break;
              renderHistory(data.operations || []);
              if (updated.status !== 'queued') {
                pixFeedback.textContent = messages[updated.status] || updated.message || updated.status;
                break;
              }
            }
          }
        }
      } catch (error) {
        if (selectedAccount && selectedAccount.profileId === selected.profileId &&
            selectedAccount.accountId === selected.accountId) {
          pixFeedback.textContent = 'Não foi possível cadastrar esta chave: ' + error.message;
        }
      } finally {
        preparing = false;
        updatePixPreview();
      }
    });

    function summaryRow(title, value) {
      const row = el('div', 'account-summary-row');
      row.append(el('span', 'account-summary-key', title),
        el('span', 'account-summary-value', value == null || value === '' ? '—' : String(value)));
      return row;
    }

    function clearSelection(message) {
      ++detailRevision;
      selectedId = null;
      selectedUuid = null;
      selectedAccount = null;
      ++withdrawalRevision;
      withdrawMessage.textContent = '';
      withdrawHistory.replaceChildren();
      updateWithdrawButton();
      submitWithdraw.disabled = true;
      refreshOrders.disabled = true;
      orders.replaceChildren();
      ++ordersRevision;
      selector.value = '';
      details.replaceChildren();
      hint.hidden = false;
      feedback.textContent = message || '';
      ++historyRevision;
      history.replaceChildren();
      updatePixPreview();
    }

    function showDetail(detail) {
      // Only the existing local-profile endpoints are read. This snapshot is
      // display context, NOT proof of an active Chromium/authenticated session.
      const verified = detail.platformUserId;
      const observed = detail.platformObservedUserId;
      const remoteId = verified || observed || null;
      const kind = verified ? 'verified' : observed ? 'observed' : 'unidentified';
      selectedAccount = Object.freeze({
        profileId: detail.id,
        accountId: detail.accountId,
        platformId: remoteId ? String(remoteId) : null,
        identityStatus: kind,
      });
      details.replaceChildren(
        summaryRow('Conta', detail.label || 'Conta ' + detail.id),
        summaryRow('ID interno', detail.id),
        summaryRow('UUID da conta', detail.accountId),
        summaryRow('ID da plataforma', remoteId || 'Não identificado'),
        summaryRow('Username', detail.username || 'Não informado'),
        summaryRow('Nome completo', (detail.financialRealName && detail.financialCpf)
          ? detail.financialRealName : (detail.fullName || 'Não informado')),
        summaryRow('CPF', ((detail.financialRealName && detail.financialCpf)
          ? detail.financialCpf : detail.cpf) ? 'Salvo nos detalhes' : 'Não informado'),
        summaryRow('PIN de saque', detail.hasWithdrawPin ? 'Protegido no cofre Windows' : 'Salve um PIN em Contas antes de sacar'),
        summaryRow('Identificação', kind === 'verified' ? 'Verificado' :
          kind === 'observed' ? 'Observado, não verificado' : 'Não identificado')
      );
      hint.hidden = true;
      updatePixPreview();
      loadHistory();
      loadWithdrawHistory();
      updateWithdrawButton();
      loadOrders();
      feedback.textContent = kind === 'unidentified'
        ? 'Esta conta ainda não possui um ID da plataforma identificado.'
        : 'Dados da conta carregados. Selecione uma chave Pix abaixo.';
    }

    async function loadDetail(profileId, uuid) {
      const revision = ++detailRevision;
      selectedAccount = null;
      updateWithdrawButton();
      submitWithdraw.disabled = true;
      refreshOrders.disabled = true;
      details.replaceChildren();
      hint.hidden = false;
      feedback.textContent = 'Carregando identificação...';
      try {
        const detail = await get('/api/local-profiles/detail?id=' + encodeURIComponent(profileId));
        if (revision !== detailRevision || selectedId !== profileId || selectedUuid !== uuid) return;
        // The internal id and account UUID must refer to the SAME record
        // in the list and detail reply. Never bind a remote UID to another card.
        if (!detail || detail.id !== profileId || detail.accountId !== uuid) {
          clearSelection('Dados de conta divergentes. Atualize a lista antes de selecionar novamente.');
          return;
        }
        showDetail(detail);
      } catch (error) {
        if (revision === detailRevision && selectedId === profileId) {
          selectedAccount = null;
          updateWithdrawButton();
          feedback.textContent = 'Não foi possível carregar a conta: ' + error.message;
        }
      }
    }

    function chooseAccount(idValue) {
      const profileId = Number(idValue);
      const item = profiles.find(p => p.id === profileId && idValue !== '');
      if (!item) {
        clearSelection('');
        return;
      }
      selectedId = item.id;
      selectedUuid = item.accountId;
      ++historyRevision;
      ++withdrawalRevision;
      history.replaceChildren();
      withdrawHistory.replaceChildren();
      orders.replaceChildren();
      submitWithdraw.disabled = true;
      ++ordersRevision;
      withdrawMessage.textContent = '';
      updatePixPreview();
      updateWithdrawButton();
      loadDetail(selectedId, selectedUuid);
    }

    selector.addEventListener('change', () => chooseAccount(selector.value));
    refreshButton.addEventListener('click', () => {
      refresh();
      loadPixKeys();
    });

    async function refresh() {
      const revision = ++listRevision;
      try {
        const data = await get('/api/local-profiles');
        if (revision !== listRevision) return;
        profiles = (Array.isArray(data.profiles) ? data.profiles : []).filter(
          p => p && Number.isSafeInteger(p.id) && p.id > 0 &&
            typeof p.accountId === 'string' && p.accountId.length > 0
        );
        count.textContent = profiles.length + (profiles.length === 1 ? ' conta' : ' contas');
        const previousId = selectedId, previousUuid = selectedUuid;
        selector.replaceChildren(new Option(
          profiles.length ? 'Selecione uma conta' : 'Nenhuma conta cadastrada', ''
        ));
        for (const p of profiles) {
          const option = new Option(
            (p.label || 'Conta ' + p.id) + ' — ' + (p.username || 'sem username'),
            String(p.id)
          );
          selector.append(option);
        }
        const remains = profiles.some(p => p.id === previousId && p.accountId === previousUuid);
        if (remains) {
          selector.value = String(previousId);
          await loadDetail(previousId, previousUuid);
        } else {
          clearSelection(previousId !== null
            ? 'A conta selecionada não está mais disponível.' : '');
        }
      } catch (error) {
        if (revision === listRevision) {
          // Do not continue presenting previously fetched identity on a
          // failed refresh; future operations must always resolve afresh.
          selectedAccount = null;
          details.replaceChildren();
          hint.hidden = false;
          feedback.textContent = 'Não foi possível atualizar as contas: ' + error.message;
        }
      }
    }

    // Future operation modules may receive this read-only, typed identity
    // context. Every future mutating operation MUST revalidate association
    // server-side before sending requests to an external service.
    function getSelectedAccount() {
      return selectedAccount ? {...selectedAccount} : null;
    }

    return Object.freeze({node, onShow: () => {
      refresh();
      loadPixKeys();
    }, getSelectedAccount});
  }

  Object.defineProperty(window, 'NexoraOperationsView', {
    value: Object.freeze({create}), writable: false, configurable: false,
  });
})();
