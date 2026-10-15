/* Nexora: list local profiles only. */
'use strict';
(function () {
  function create({get}) {
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

    function render(profiles) {
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
        head.append(heading, origin);
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
        if (current === generation) render(Array.isArray(data.profiles) ? data.profiles : []);
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
