/* Operations workspace: local Pix binding preparation only, without external execution. */
'use strict';
(function () {
  // Future implementations live in separate modules; the catalog only
  // describes reserved interface slots and has NO execute handlers.
  const CATALOG = Object.freeze([
    Object.freeze({id: 'pix-bind', title: 'Cadastrar chave Pix',
      description: 'Vinculação de chave à conta selecionada.'}),
    Object.freeze({id: 'withdraw', title: 'Saques',
      description: 'Solicitações e acompanhamento de retiradas.'}),
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
      el('p', '', 'Selecione uma conta e prepare uma chave Pix. Nenhuma solicitação financeira é enviada nesta versão.'));
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
        'Estrutura preparada para futuras integrações. Nenhuma ação financeira está disponível.'));
    const cards = el('div', 'operations-grid');
    for (const item of CATALOG) {
      const card = el('article', 'operations-card');
      card.dataset.operation = item.id;
      card.append(el('span', 'operations-card-status', 'Não disponível'),
        el('h3', '', item.title), el('p', '', item.description));
      cards.append(card);
    }
    sections.append(cards);

    const pixPanel = el('section', 'operations-panel operations-pix-panel');
    pixPanel.append(
      el('h2', '', 'Cadastrar chave Pix'),
      el('p', 'operations-hint',
        'Selecione o tipo e uma chave salva na aba Chave Pix. ' +
        'O sistema prepara um registro local; a API da plataforma ainda não está habilitada.')
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
    const acknowledgment = el('label', 'operations-ownership');
    const ownershipCheckbox = el('input');
    ownershipCheckbox.type = 'checkbox';
    ownershipCheckbox.setAttribute('aria-label', 'Confirmar autorização da chave Pix');
    acknowledgment.append(ownershipCheckbox, el('span', '',
      'Declaro que tenho autorização para utilizar esta chave Pix. ' +
      'Isso não comprova titularidade perante a plataforma.'));
    const pixFeedback = el('p', 'operations-pix-feedback');
    pixFeedback.setAttribute('role', 'status');
    const prepareButton = el('button', 'accounts-open operations-pix-prepare', 'Preparar cadastro');
    prepareButton.type = 'button';
    prepareButton.disabled = true;
    const readinessButton = el('button', 'accounts-details operations-pix-readiness', 'Verificar requisitos da API');
    readinessButton.type = 'button';
    readinessButton.disabled = true;
    const sessionButton = el('button', 'accounts-details operations-pix-session', 'Testar API na conta aberta');
    sessionButton.type = 'button';
    sessionButton.disabled = true;
    const preview = el('div', 'operations-pix-preview');
    const historyHeading = el('h3', 'operations-history-heading', 'Histórico local da conta');
    const history = el('div', 'operations-history');
    pixPanel.append(pixControls, acknowledgment, preview,
      prepareButton, readinessButton, sessionButton, pixFeedback, historyHeading, history);
    node.append(heading, selectorPanel, pixPanel, sections);

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
      const enabled = typeof post === 'function' && !preparing &&
        !!selected && !!selected.platformId && !!key && ownershipCheckbox.checked;
      prepareButton.disabled = !enabled;
      readinessButton.disabled = !selected || !key;
      // A read-only diagnostic must be usable before identity verification.
      // Financial submission still requires an independently verified ID.
      sessionButton.disabled = !selected || !selected.platformId;
      preview.replaceChildren(
        summaryRow('Conta', selected ? 'Conta ' + selected.profileId : 'Não selecionada'),
        summaryRow('Identidade', selected ? (
          selected.identityStatus === 'verified' ? 'Verificada' :
            selected.identityStatus === 'observed' ? 'Observada, não verificada' : 'Ausente'
        ) : '—'),
        summaryRow('Chave', key ? maskedKey(key) : 'Nenhuma selecionada'),
        summaryRow('Execução', 'Somente preparação local — sem envio externo')
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
        history.append(el('p', 'operations-hint', 'Nenhuma preparação registrada nesta conta.'));
        return;
      }
      for (const op of ops) {
        const entry = el('article', 'operations-history-item');
        entry.append(
          el('strong', '', 'Preparado localmente — ' + op.keyType),
          el('span', '', 'Operação: ' + op.operationId),
          el('span', '', 'Estado: ' + op.status + ' / envio remoto: não realizado'),
          el('span', '', 'Registrado em: ' + op.createdAt),
          el('small', '', (op.missingRequirements || []).join(' ')),
        );
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
      ownershipCheckbox.checked = false;
      pixFeedback.textContent = '';
      renderKeys();
    });
    keySelector.addEventListener('change', () => {
      ownershipCheckbox.checked = false;
      updatePixPreview();
    });
    ownershipCheckbox.addEventListener('change', updatePixPreview);
    sessionButton.addEventListener('click', async () => {
      const selected = getSelectedAccount();
      if (!selected || !selected.platformId) return;
      sessionButton.disabled = true;
      pixFeedback.textContent = 'Testando somente a conexão de segurança nesta conta; cadastro Pix não será enviado...';
      try {
        const response = await post('/api/operations/pix/check-session', {
          profileId: selected.profileId,
          accountId: selected.accountId
        });
        if (!selectedAccount || selectedAccount.accountId !== selected.accountId ||
            selectedAccount.profileId !== selected.profileId) return;
        pixFeedback.textContent = response.message || 'Resultado da sessão não identificado.';
      } catch (error) {
        if (selectedAccount && selectedAccount.accountId === selected.accountId) {
          pixFeedback.textContent = 'Falha ao testar a sessão: ' + error.message;
        }
      } finally {
        updatePixPreview();
      }
    });
    readinessButton.addEventListener('click', async () => {
      const selected = getSelectedAccount(), key = activeKey();
      if (!selected || !key) return;
      readinessButton.disabled = true;
      pixFeedback.textContent = 'Conferindo identidade, PIN de saque e chave vinculada à conta...';
      try {
        const result = await post('/api/operations/pix/readiness', {
          profileId: selected.profileId,
          accountId: selected.accountId,
          keyId: key.id,
          keyType: typeSelector.value
        });
        if (!selectedAccount || selectedAccount.accountId !== selected.accountId) return;
        pixFeedback.textContent = result.missingRequirements?.length
          ? 'Antes da API externa: ' + result.missingRequirements.join(' ')
          : 'Dados locais conferidos. O executor externo ainda não está disponível na sessão Windows.';
      } catch (error) {
        pixFeedback.textContent = 'Verificação não concluída: ' + error.message;
      } finally { updatePixPreview(); }
    });
    prepareButton.addEventListener('click', async () => {
      const selected = getSelectedAccount();
      const key = activeKey();
      if (!selected || !key || !ownershipCheckbox.checked ||
          typeof post !== 'function' || preparing) return;
      preparing = true;
      updatePixPreview();
      pixFeedback.textContent = 'Validando conta e chave no servidor local...';
      try {
        const result = await post('/api/operations/pix/prepare', {
          profileId: selected.profileId,
          accountId: selected.accountId,
          keyId: key.id,
          keyType: typeSelector.value,
          ownershipAcknowledged: true
        });
        if (selectedAccount && selectedAccount.profileId === selected.profileId &&
            selectedAccount.accountId === selected.accountId) {
          pixFeedback.textContent = result.reused
            ? 'Preparação existente recuperada. Nenhuma requisição externa foi enviada.'
            : 'Preparação salva localmente. Integração da plataforma ainda não disponível.';
          await loadHistory();
        }
      } catch (error) {
        pixFeedback.textContent = 'Falha na preparação: ' + error.message;
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
      selector.value = '';
      details.replaceChildren();
      hint.hidden = false;
      feedback.textContent = message || '';
      ownershipCheckbox.checked = false;
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
        summaryRow('Nome real para operações', detail.financialRealName || 'Não confirmado; ajuste na aba Contas'),
        summaryRow('CPF financeiro', detail.financialCpf ? 'Salvo nos detalhes da conta' : 'Não confirmado'),
        summaryRow('PIN de saque', detail.hasWithdrawPin ? 'Protegido no Windows' : 'Não salvo'),
        summaryRow('Identificação', kind === 'verified' ? 'Verificado' :
          kind === 'observed' ? 'Observado, não verificado' : 'Não identificado')
      );
      hint.hidden = true;
      updatePixPreview();
      loadHistory();
      feedback.textContent = kind === 'unidentified'
        ? 'Esta conta ainda não possui um ID da plataforma identificado.'
        : 'Identificação carregada somente para consulta. Operações ainda indisponíveis.';
    }

    async function loadDetail(profileId, uuid) {
      const revision = ++detailRevision;
      selectedAccount = null;
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
      ownershipCheckbox.checked = false;
      ++historyRevision;
      history.replaceChildren();
      updatePixPreview();
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
