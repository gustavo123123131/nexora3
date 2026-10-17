/* Operations workspace: account selection only. No financial actions or endpoints. */
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

  function create({get}) {
    const node = el('section', 'operations-local');
    const heading = el('header', 'operations-heading');
    const intro = el('div');
    intro.append(el('h1', '', 'Operações'),
      el('p', '', 'Selecione uma conta para consultar sua identificação. Nenhuma operação está ativa nesta etapa.'));
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
    node.append(heading, selectorPanel, sections);

    let listRevision = 0;
    let detailRevision = 0;
    let selectedId = null;   // integer LocalProfileStore id, never remote UID
    let selectedUuid = null; // immutable internal accountId UUID
    let selectedAccount = null;
    let profiles = [];

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
        summaryRow('Identificação', kind === 'verified' ? 'Verificado' :
          kind === 'observed' ? 'Observado, não verificado' : 'Não identificado')
      );
      hint.hidden = true;
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
      loadDetail(selectedId, selectedUuid);
    }

    selector.addEventListener('change', () => chooseAccount(selector.value));
    refreshButton.addEventListener('click', () => refresh());

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

    return Object.freeze({node, onShow: refresh, getSelectedAccount});
  }

  Object.defineProperty(window, 'NexoraOperationsView', {
    value: Object.freeze({create}), writable: false, configurable: false,
  });
})();
