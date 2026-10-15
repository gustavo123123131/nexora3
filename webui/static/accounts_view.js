/* Nexora / cadastro e contas. Independent client UI; no direct site APIs.
 * All account operations use the authenticated Windows localhost agent.
 */
'use strict';
(function () {
  const fieldNames = [
    ['username', 'Usuário'], ['email', 'E-mail'], ['phone', 'Telefone'],
    ['password', 'Senha'], ['confirmPassword', 'Confirmar senha'],
    ['fullName', 'Nome completo'], ['cpf', 'CPF'],
  ];
  const statusName = {
    local: 'Registro local', pending: 'Pendente',
    completed: 'Confirmado pelo site', failed: 'Falha',
  };

  function element(tag, cls, text) {
    const item = document.createElement(tag);
    if (cls) item.className = cls;
    if (text !== undefined) item.textContent = String(text);
    return item;
  }
  function labelInput(text, name, value, props = {}) {
    const wrap = element('label', 'accounts-field');
    wrap.append(element('span', '', text));
    const input = element('input');
    input.name = name;
    input.value = value || '';
    for (const [key, val] of Object.entries(props)) input[key] = val;
    wrap.append(input);
    return wrap;
  }
  function notify(view, message, error = false) {
    const slot = view.querySelector('.accounts-message');
    slot.textContent = message;
    slot.className = 'accounts-message ' + (error ? 'is-error' : 'is-ok');
    slot.hidden = !message;
  }
  function options(select, entries, first) {
    select.replaceChildren();
    if (first) {
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = first;
      select.append(opt);
    }
    for (const [value, label] of entries) {
      const opt = document.createElement('option');
      opt.value = String(value);
      opt.textContent = label;
      select.append(opt);
    }
  }

  function create({ get, post }) {
    const root = element('div', 'accounts-workspace');
    root.innerHTML = [
      '<div class="accounts-heading"><div>',
      '<div class="workspace-eyebrow">WORKSPACE / CADASTRO</div>',
      '<h1>Contas e cadastro</h1>',
      '<p>Gerencie contas próprias, perfis existentes e cadastros autorizados.</p>',
      '</div><button type="button" class="accounts-refresh">↻ Atualizar</button></div>',
      '<div class="accounts-notice">Plataformas e contas são guardadas em accounts.json no agente Windows. ',
      'Perfis, registros e status ficam neste computador. ',
      'Cadastros exigem revisão e verificações oficiais do site.</div>',
      '<p class="accounts-message" role="status" hidden></p>',
      '<section class="accounts-main-grid">',
      '<article class="accounts-panel">',
      '<header><h2>01 · Configurar site</h2><p>Informe a URL e os seletores reais do formulário.</p></header>',
      '<form id="nexora-site-form" autocomplete="off">',
      '<div class="accounts-two">',
      '<label>ID do site<input name="id" required maxlength="64" placeholder="meu_site"></label>',
      '<label>Identificação<input name="displayName" required maxlength="80" placeholder="Site autorizado"></label></div>',
      '<label>URL oficial<input name="baseUrl" required maxlength="2048" placeholder="https://exemplo.com"></label>',
      '<div class="accounts-two">',
      '<label>Rota de cadastro<input name="registrationPath" required value="/register"></label>',
      '<label>Rota de login<input name="loginPath" required value="/login"></label></div>',
      '<details class="accounts-detail"><summary>Campos do formulário (seletores CSS)</summary>',
      '<div class="accounts-fields" id="nexora-field-selectors"></div>',
      '</details>',
      '<label>Seletor do botão de envio<input name="submitSelector" maxlength="200" placeholder="button[type=submit]"></label>',
      '<label>Seletor de confirmação inequívoca (opcional)<input name="successSelector" maxlength="200" placeholder=".registro-confirmado"></label>',
      '<label class="accounts-consent"><input type="checkbox" name="authorized">',
      '<span>Confirmo que tenho autorização para usar o cadastro deste site.</span></label>',
      '<button type="submit" class="accounts-primary">Salvar no JSON local</button>',
      '</form></article>',
      '<article class="accounts-panel">',
      '<header><h2>02 · Adicionar conta</h2><p>Vincule a um perfil do Windows ou crie registros em lote.</p></header>',
      '<form id="nexora-account-form" autocomplete="off">',
      '<label>Site cadastrado<select name="siteId" required></select></label>',
      '<label>Nome interno<input name="displayName" required maxlength="120" placeholder="Minha conta 01"></label>',
      '<label>Identificador de login (opcional)<input name="loginIdentifier" maxlength="128" autocomplete="off" placeholder="Usuário de referência"></label>',
      '<div class="accounts-two"><label>Tipo<select name="profileScope">',
      '<option value="mae">Conta mãe</option><option value="filha">Conta filha</option></select></label>',
      '<label>Modo<select name="profileMode"><option value="desktop">Computador</option>',
      '<option value="android">Android</option><option value="iphone">iPhone</option>',
      '<option value="mixed">Android/iPhone alternados</option></select></label></div>',
      '<div class="accounts-two"><label>Número do perfil (conta única)<input type="number" name="profileIndex" min="1" max="999" value="1" required></label>',
      '<label>Conta mãe vinculada<select name="parentId"><option value="">Sem vínculo</option></select></label></div>',
      '<label>Quantidade de registros (1 a 100)<input type="number" name="quantity" min="1" max="100" value="1" required></label>',
      '<p class="accounts-tip">Em lote, nomes e números sequenciais são definidos automaticamente. ',
      'O mesmo tipo, modo e número acessam o diretório ',
      'de perfil já usado no multi-login. Feche uma janela desse perfil antes de reabri-la por aqui.</p>',
      '<button type="submit" class="accounts-primary">Salvar no JSON local</button>',
      '</form></article>',
      '</section>',
      '<section class="accounts-panel accounts-directory">',
      '<header><div><h2>03 · Suas contas</h2><p>Abrir perfil, preparar formulário, verificar pendências.</p></div>',
      '<div class="accounts-directory-tools"><input type="search" aria-label="Buscar contas" placeholder="Buscar..." class="accounts-search">',
      '<select aria-label="Filtrar situação" class="accounts-status-filter">',
      '<option value="">Todas</option><option value="local">Locais</option><option value="pending">Pendentes</option>',
      '<option value="completed">Confirmadas</option><option value="failed">Falhas</option></select></div></header>',
      '<div class="accounts-table-scroll"><table><thead><tr><th>Conta</th><th>Perfil</th><th>Status</th><th>Ações</th></tr></thead>',
      '<tbody class="accounts-list"></tbody></table></div>',
      '</section>',
      '<section class="accounts-panel accounts-review" hidden aria-live="polite">',
      '<header><div><h2>04 · Revisar cadastro</h2><p>Campos detectados no formulário aberto no Windows.</p></div>',
      '<button type="button" class="accounts-close-review">Fechar revisão</button></header>',
      '<div class="accounts-review-body"><p class="accounts-review-info"></p>',
      '<form class="accounts-submit-form" autocomplete="off"><div class="accounts-submit-fields"></div>',
      '<button type="submit" class="accounts-primary">Revisar e confirmar envio</button></form></div>',
      '</section>',
    ].join('');

    const siteForm = root.querySelector('#nexora-site-form');
    const accountForm = root.querySelector('#nexora-account-form');
    const siteSelect = accountForm.elements.namedItem('siteId');
    const parentSelect = accountForm.elements.namedItem('parentId');
    const list = root.querySelector('.accounts-list');
    const review = root.querySelector('.accounts-review');
    const reviewForm = root.querySelector('.accounts-submit-form');
    const reviewFields = root.querySelector('.accounts-submit-fields');
    const fieldsContainer = root.querySelector('.accounts-fields');
    const selectKeys = new Map();

    for (const [key, text] of fieldNames) {
      const wrap = element('div', 'accounts-selector-row');
      const box = labelInput(text, key, '', {placeholder: 'Seletor CSS — opcional', maxLength: 200});
      const required = element('label', 'accounts-required');
      const check = element('input');
      check.type = 'checkbox';
      check.dataset.required = key;
      required.append(check, element('span', '', 'Obrigatório'));
      wrap.append(box, required);
      fieldsContainer.append(wrap);
      selectKeys.set(key, {field: box.querySelector('input'), required: check});
    }

    let sites = [], accounts = [], activeReview = null;
    let busy = false, lastShown = 0;
    const safeRun = async (fn) => {
      if (busy) return;
      busy = true;
      notify(root, '');
      try { await fn(); }
      catch (error) { notify(root, error.message || 'Operação indisponível.', true); }
      finally { busy = false; }
    };

    function redraw() {
      options(siteSelect, sites.map(s => [s.id, s.displayName]));
      options(parentSelect, accounts.filter(a => a.profileScope === 'mae').map(a => [a.id, a.displayName]), 'Sem vínculo');
      const query = root.querySelector('.accounts-search').value.trim().toLowerCase();
      const status = root.querySelector('.accounts-status-filter').value;
      const visible = accounts.filter(a =>
        (!status || a.status === status) &&
        (!query || [a.displayName, a.siteId, a.loginIdentifier].join(' ').toLowerCase().includes(query)));
      list.replaceChildren();
      if (!visible.length) {
        const tr = element('tr');
        const td = element('td', 'accounts-empty', 'Nenhuma conta encontrada. Salve um site e adicione sua primeira conta.');
        td.colSpan = 4; tr.append(td); list.append(tr); return;
      }
      for (const acc of visible) {
        const tr = element('tr');
        const first = element('td');
        first.append(element('strong', '', acc.displayName),
          element('small', '', acc.siteId + (acc.parentId ? ' · Filha' : ' · Mãe')));
        const profile = element('td', '', acc.profileScope + ' · ' + acc.profileMode + ' #' + acc.profileIndex);
        const stat = element('td');
        stat.append(element('span', 'accounts-status accounts-status-' + acc.status,
          statusName[acc.status] || acc.status));
        const buttons = element('td', 'accounts-actions');
        for (const [action, title] of [
          ['open', 'Abrir perfil'], ['prepare', 'Preparar cadastro'],
          ['refresh', 'Verificar'], ['delete', 'Excluir'],
        ]) {
          const button = element('button', 'accounts-small-button', title);
          button.type = 'button'; button.dataset.action = action; button.dataset.key = acc.id;
          if (action === 'prepare' && !sites.find(s => s.id === acc.siteId)?.authorized) {
            button.disabled = true; button.title = 'Site sem configuração de cadastro autorizada';
          }
          buttons.append(button);
        }
        tr.append(first, profile, stat, buttons);
        list.append(tr);
      }
    }

    async function refresh() {
      const [s, a] = await Promise.all([
        get('/api/registration/sites'), get('/api/registration/accounts'),
      ]);
      sites = s.sites || []; accounts = a.accounts || [];
      redraw();
    }

    siteForm.addEventListener('submit', event => {
      event.preventDefault();
      void safeRun(async () => {
        const form = new FormData(siteForm);
        const fields = {};
        for (const [key, ref] of selectKeys) {
          const selector = ref.field.value.trim();
          if (selector) fields[key] = {selector, required: ref.required.checked};
        }
        const site = {
          id: form.get('id'), displayName: form.get('displayName'),
          baseUrl: form.get('baseUrl'), registrationPath: form.get('registrationPath'),
          loginPath: form.get('loginPath'), authorized: form.get('authorized') === 'on',
          submitSelector: form.get('submitSelector'), successSelector: form.get('successSelector'),
          fields,
        };
        const result = await post('/api/registration/sites', site);
        siteForm.reset();
        await refresh();
        siteSelect.value = result.site.id;
        notify(root, 'Site salvo no JSON local do Windows.');
      });
    });

    accountForm.addEventListener('submit', event => {
      event.preventDefault();
      void safeRun(async () => {
        const form = new FormData(accountForm);
        const result = await post('/api/registration/accounts', {
          siteId: form.get('siteId'), displayName: form.get('displayName'),
          loginIdentifier: form.get('loginIdentifier'), parentId: form.get('parentId'),
          profileScope: form.get('profileScope'), profileMode: form.get('profileMode'),
          profileIndex: Number(form.get('profileIndex')),
          quantity: Number(form.get('quantity')),
        });
        accountForm.elements.namedItem('displayName').value = '';
        accountForm.elements.namedItem('loginIdentifier').value = '';
        await refresh();
        notify(root, result.quantity > 1 ? result.quantity + ' contas criadas no JSON local.' : 'Conta ' + result.account.displayName + ' salva no JSON local.');
      });
    });

    list.addEventListener('click', event => {
      const button = event.target.closest('button[data-action]');
      if (!button) return;
      const action = button.dataset.action, accountId = button.dataset.key;
      void safeRun(async () => {
        if (action === 'delete') {
          if (!confirm('Excluir o registro local? O perfil Chromium e a conta externa serão preservados.')) return;
          await post('/api/registration/accounts/delete', {accountId});
          review.hidden = true; activeReview = null;
          notify(root, 'Registro removido do JSON local. O perfil Chromium não foi apagado.');
        } else if (action === 'open') {
          await post('/api/registration/accounts/open', {accountId});
          notify(root, 'Chromium solicitado no seu Windows, usando o perfil original.');
        } else if (action === 'prepare') {
          const result = await post('/api/registration/prepare', {accountId});
          activeReview = accountId;
          showReview(result);
          notify(root, 'Formulário aberto. Confira os campos antes de enviar.');
        } else if (action === 'refresh') {
          const result = await post('/api/registration/refresh', {accountId});
          notify(root, 'Situação: ' + (statusName[result.account.status] || result.account.status) +
            '. Uma verificação pendente não significa cadastro concluído.');
        }
        await refresh();
      });
    });

    function showReview(data) {
      review.hidden = false;
      reviewFields.replaceChildren();
      const missing = data.missingRequired || [];
      const info = root.querySelector('.accounts-review-info');
      info.textContent = 'Página: ' + data.registrationUrl +
        (missing.length ? ' · Campos obrigatórios não encontrados: ' + missing.join(', ') :
          ' · Campos obrigatórios detectados. Revise os dados antes de enviar.');
      for (const field of data.fields || []) {
        if (!field.present) continue;
        const meta = fieldNames.find(x => x[0] === field.name);
        reviewFields.append(labelInput((meta ? meta[1] : field.name) + (field.required ? ' *' : ''),
          field.name, '', {type: field.name.toLowerCase().includes('password') ? 'password' : 'text',
            required: !!field.required, autoComplete: 'off', maxLength: 256}));
      }
      reviewForm.querySelector('button[type=submit]').disabled = missing.length > 0;
      review.scrollIntoView({behavior: 'smooth', block: 'start'});
    }

    reviewForm.addEventListener('submit', event => {
      event.preventDefault();
      if (!activeReview) return;
      const accountId = activeReview;
      if (!confirm('Você revisou os dados e autoriza o envio pelo formulário oficial do site?')) return;
      void safeRun(async () => {
        const values = {};
        for (const [name, value] of new FormData(reviewForm)) values[name] = value;
        try {
          const result = await post('/api/registration/submit', {
            accountId, values, userConfirmed: true,
          });
          if (result.errors) throw new Error(Object.values(result.errors).join(' · '));
          notify(root, 'Situação: ' + (statusName[result.account.status] || 'Pendente') +
            '. Se houver SMS/CAPTCHA/e-mail, finalize a verificação manualmente.');
          await refresh();
          if (result.account.status === 'completed') {
            review.hidden = true; activeReview = null;
          }
        } finally {
          // Credentials must not remain in the form.
          reviewForm.reset();
          for (const key of Object.keys(values)) values[key] = '';
        }
      });
    });

    root.querySelector('.accounts-close-review').addEventListener('click', () => {
      review.hidden = true; activeReview = null; reviewForm.reset();
    });
    root.querySelector('.accounts-refresh').addEventListener('click', () => {
      void safeRun(async () => { await refresh(); notify(root, 'Lista atualizada.'); });
    });
    root.querySelector('.accounts-search').addEventListener('input', redraw);
    root.querySelector('.accounts-status-filter').addEventListener('change', redraw);
    accountForm.elements.namedItem('profileScope').addEventListener('change', event => {
      parentSelect.disabled = event.target.value !== 'filha';
      if (parentSelect.disabled) parentSelect.value = '';
    });
    parentSelect.disabled = true;

    return {
      node: root,
      onShow() {
        if (Date.now() - lastShown < 3000 || busy) return;
        lastShown = Date.now();
        void safeRun(refresh);
      },
    };
  }

  Object.defineProperty(window, 'NexoraAccountsView', {
    value: Object.freeze({create}), configurable: false, writable: false,
  });
})();
