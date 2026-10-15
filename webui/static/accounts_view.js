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

    function render(profiles, selectedId) {
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
        card.append(head, details);
        list.appendChild(card);
      }
    }

    let generation = 0;
    async function refresh() {
      const current = ++generation;
      try {
        const data = await get('/api/local-profiles');
        if (current === generation) render(Array.isArray(data.profiles) ? data.profiles : [], data.selectedProfileId ?? null);
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
