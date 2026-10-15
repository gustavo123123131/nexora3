/* Nexora: list local profiles only. */
'use strict';
(function () {
  function create({get, post}) {
    const node = document.createElement('section');
    node.className = 'accounts-local';
    const title = document.createElement('h1');
    title.textContent = 'Contas';
    const subtitle = document.createElement('p');
    subtitle.textContent = 'Perfis criados em Conta Mãe e Conta Filha.';
    const status = document.createElement('p');
    status.className = 'accounts-status';
    status.setAttribute('role', 'status');
    const list = document.createElement('div');
    list.className = 'accounts-local-grid';
    node.append(title, subtitle, status, list);

    const fields = [
      ['fullName', 'Nome completo'],
      ['username', 'Username'],
      ['email', 'E-mail'],
      ['phone', 'Telefone'],
      ['cpf', 'CPF'],
      ['password', 'Senha'],
      ['confirmPassword', 'Confirmar senha'],
    ];

    function render(profiles, selectedId, proxies, bindings) {
      list.replaceChildren();
      status.textContent = profiles.length
        ? profiles.length + (profiles.length === 1 ? ' conta salva' : ' contas salvas')
        : 'Nenhuma conta criada.';
      for (const profile of profiles) {
        const card = document.createElement('article');
        card.className = 'accounts-local-card';
        const head = document.createElement('header');
        const heading = document.createElement('h2');
        heading.textContent = profile.label;
        const origin = document.createElement('span');
        origin.textContent = profile.origin === 'mae' ? 'Conta Mãe' : 'Conta Filha';
        const select = document.createElement('button');
        select.type = 'button';
        const chosen = profile.id === selectedId;
        select.textContent = chosen ? 'Selecionada — limpar' : 'Selecionar';
        select.className = 'accounts-select';
        select.setAttribute('aria-pressed', chosen ? 'true' : 'false');
        select.addEventListener('click', async () => {
          select.disabled = true;
          try {
            await post('/api/local-profiles/select', {id: chosen ? null : profile.id});
            await refresh();
          } catch (error) {
            status.textContent = 'Não foi possível selecionar: ' + error.message;
          } finally {
            select.disabled = false;
          }
        });
        head.append(heading, origin, select);
        // Compact persistent association; the Proxy section stays one-input only.
        const proxyRow = document.createElement('div');
        proxyRow.className = 'accounts-proxy-association';
        const proxyLabel = document.createElement('label');
        const proxySelect = document.createElement('select');
        proxySelect.setAttribute('aria-label', 'Proxy da ' + profile.label);
        proxyLabel.textContent = 'Proxy';
        const automatic = document.createElement('option');
        automatic.value = '';
        automatic.textContent = 'Automática na próxima abertura';
        proxySelect.append(automatic);
        for (const proxy of proxies) {
          const option = document.createElement('option');
          option.value = proxy.id;
          option.textContent = proxy.protocol.toUpperCase() + ' — ' + proxy.host + ':' + proxy.port;
          proxySelect.append(option);
        }
        proxySelect.value = bindings[String(profile.id)] || '';
        proxySelect.addEventListener('change', async () => {
          proxySelect.disabled = true;
          try {
            await post('/api/proxies/bind-profile', {
              profileId: profile.id, proxyId: proxySelect.value || null
            });
            status.textContent = 'Proxy vinculada à ' + profile.label + '.';
          } catch (error) {
            status.textContent = 'Não foi possível salvar a proxy: ' + error.message;
          } finally {
            proxySelect.disabled = false;
            await refresh();
          }
        });
        proxyRow.append(proxyLabel, proxySelect);
        const details = document.createElement('dl');
        for (const [key, label] of fields) {
          const row = document.createElement('div');
          const dt = document.createElement('dt');
          const dd = document.createElement('dd');
          dt.textContent = label;
          dd.textContent = String(profile[key] ?? '');
          row.append(dt, dd);
          details.appendChild(row);
        }
        card.append(head, proxyRow, details);
        list.appendChild(card);
      }
    }

    let generation = 0;
    async function refresh() {
      const current = ++generation;
      try {
        const [data, proxyData, bindingData] = await Promise.all([
          get('/api/local-profiles'), get('/api/proxies'), get('/api/proxies/profile-bindings')
        ]);
        if (current === generation) render(
          Array.isArray(data.profiles) ? data.profiles : [],
          data.selectedProfileId ?? null,
          Array.isArray(proxyData.proxies) ? proxyData.proxies : [],
          bindingData.bindings || {}
        );
      } catch (error) {
        if (current === generation) status.textContent = 'Não foi possível carregar os perfis: ' + error.message;
      }
    }
    return {node, onShow: refresh};
  }

  Object.defineProperty(window, 'NexoraAccountsView', {
    value: Object.freeze({create}), writable: false, configurable: false,
  });
})();
