/* Nexora proxy workspace. Uses authenticated local-agent APIs only.
 * ProxyBet preload evidence: proxies:list, proxies:save and proxy:test.
 */
'use strict';
(function () {
  const template = `
    <div class="proxy-workspace" aria-labelledby="proxy-page-title">
      <div class="proxy-page-heading">
        <div>
          <div class="workspace-eyebrow"><span class="eyebrow-line"></span> WORKSPACE / REDE</div>
          <h1 id="proxy-page-title">Gerenciamento de proxy</h1>
          <p>Um espaço dedicado à configuração de conexões para seus perfis.</p>
        </div>
        <div class="proxy-state-tag"><span class="state-dot"></span> GERENCIADOR LOCAL</div>
      </div>
      <div class="proxy-notice" role="status">
        <span class="proxy-notice-icon" aria-hidden="true">i</span>
        <div>
          <strong>Proxies para o Chromium</strong>
          <p>Salve, teste a porta TCP e escolha o proxy padrão para Mãe e Filha. Troque o proxy com os navegadores fechados: novas requisições em janelas HTTP já abertas podem seguir outra rota.</p>
        </div>
      </div>
      <div class="proxy-layout">
        <section class="proxy-panel proxy-config" aria-labelledby="proxy-config-title">
          <header class="proxy-panel-heading">
            <div class="proxy-panel-icon" aria-hidden="true">01</div>
            <div><h2 id="proxy-config-title">Detalhes do proxy</h2><p>HTTP, HTTPS ou SOCKS5. Dados salvos apenas no agente local.</p></div>
          </header>
          <form id="proxy-preview-form" class="proxy-form" autocomplete="off" novalidate>
            <div class="proxy-field">
              <label for="proxy-name">Identificação <span class="proxy-field-note">Nome interno</span></label>
              <input id="proxy-name" name="label" type="text" placeholder="Ex.: Proxy principal" spellcheck="false" autocomplete="off">
            </div>
            <div class="proxy-fields-two">
              <div class="proxy-field">
                <label for="proxy-host">Servidor / host</label>
                <input id="proxy-host" name="host" type="text" placeholder="proxy.exemplo.com" spellcheck="false" autocomplete="off">
              </div>
              <div class="proxy-field">
                <label for="proxy-port">Porta</label>
                <input id="proxy-port" name="port" type="text" inputmode="numeric" placeholder="8080" spellcheck="false" autocomplete="off">
              </div>
            </div>
            <div class="proxy-field">
              <label for="proxy-protocol">Protocolo</label>
              <div class="proxy-select-wrap">
                <select id="proxy-protocol" name="protocol" aria-describedby="proxy-protocol-hint">
                  <option value="http">HTTP</option><option value="https">HTTPS</option>
                  <option value="socks5">SOCKS5</option>
                </select>
              </div>
              <small id="proxy-protocol-hint">HTTPS e SOCKS5 usam a configuração nativa do navegador; HTTP utiliza o gateway local existente.</small>
            </div>
            <div class="proxy-fields-two">
              <div class="proxy-field">
                <label for="proxy-user">Usuário <span class="proxy-field-note">Opcional</span></label>
                <input id="proxy-user" name="username" type="text" placeholder="Usuário do proxy" autocomplete="off" spellcheck="false">
              </div>
              <div class="proxy-field">
                <label for="proxy-password">Senha <span class="proxy-field-note">Opcional</span></label>
                <div class="proxy-password-box">
                  <input id="proxy-password" name="password" type="password" placeholder="Senha do proxy" autocomplete="new-password" spellcheck="false">
                  <button type="button" class="proxy-password-toggle" aria-label="Mostrar senha" aria-pressed="false" title="Mostrar senha">
                    <svg aria-hidden="true" width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.6-6 10-6 10 6 10 6-3.6 6-10 6-10-6-10-6Z"/><circle cx="12" cy="12" r="3"/></svg>
                  </button>
                </div>
              </div>
            </div>
            <div class="proxy-form-footer">
              <div><strong id="proxy-form-mode">Novo proxy</strong><span>Senha guardada no agente local; nunca é enviada na listagem.</span></div>
              <button class="proxy-save-button" type="submit">Salvar proxy <span aria-hidden="true">↗</span></button>
            </div>
          </form>
        </section>
        <section class="proxy-panel proxy-directory" aria-labelledby="proxy-list-title">
          <header class="proxy-panel-heading">
            <div class="proxy-panel-icon" aria-hidden="true">02</div>
            <div><h2 id="proxy-list-title">Proxies cadastrados</h2><p>Escolha um proxy para as próximas janelas.</p></div>
            <span class="proxy-count">0 registros</span>
          </header>
          <div class="proxy-table-wrap">
            <table class="proxy-table">
              <thead><tr><th scope="col">Nome</th><th scope="col">Servidor</th><th scope="col">Protocolo</th><th scope="col">Status</th><th scope="col">Ações</th></tr></thead>
              <tbody><tr><td class="proxy-empty-cell" colspan="5">
                <div class="proxy-empty-icon" aria-hidden="true">
                  <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/><path d="M7 6.5h.01M7 17.5h.01M11 10v4M17 10v4"/></svg>
                </div>
                <strong>Nenhum proxy configurado</strong>
                <p>Preencha o formulário e clique em Salvar proxy.</p>
              </td></tr></tbody>
            </table>
          </div>
          <footer class="proxy-directory-footer"><span class="proxy-inactive-dot"></span> <span id="proxy-active-label">Nenhum proxy ativo</span><button type="button" id="proxy-clear-active" class="proxy-action" disabled>Desativar</button></footer>
        </section>
      </div>
    </div>
  `;
  // All dynamic data is rendered with textContent, never inserted as HTML.
  function create(api) {
    const view = document.createElement('div');
    view.className = 'proxy-client-view';
    view.innerHTML = template; // Static developer-owned markup.
    const form = view.querySelector('#proxy-preview-form');
    const password = view.querySelector('#proxy-password');
    const toggle = view.querySelector('.proxy-password-toggle');
    const table = view.querySelector('.proxy-table tbody');
    const activeLabel = view.querySelector('#proxy-active-label');
    const disableActive = view.querySelector('#proxy-clear-active');
    const count = view.querySelector('.proxy-count');
    const mode = view.querySelector('#proxy-form-mode');
    const saveButton = view.querySelector('.proxy-save-button');
    const status = document.createElement('p');
    status.className = 'proxy-feedback';
    status.setAttribute('role', 'status');
    form.appendChild(status);

    let list = [];
    let activeId = null;
    let editing = null;
    let busy = false;
    let lastFetch = 0;

    function show(message, isError = false) {
      status.textContent = message;
      status.classList.toggle('proxy-error', isError);
    }
    function value(id) { return view.querySelector('#proxy-' + id).value.trim(); }
    function setValue(id, value) { view.querySelector('#proxy-' + id).value = value || ''; }
    function resetForm() {
      editing = null;
      form.reset();
      mode.textContent = 'Novo proxy';
      saveButton.textContent = 'Salvar proxy ↗';
      password.placeholder = 'Senha do proxy';
      show('');
    }
    function actionButton(text, fn, disabled) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'proxy-action';
      button.textContent = text;
      button.disabled = !!disabled;
      button.addEventListener('click', fn);
      return button;
    }
    async function invoke(path, data, successMessage) {
      if (busy) return null;
      busy = true;
      try {
        const result = await api.post(path, data);
        if (successMessage) show(successMessage);
        await refresh(true);
        return result;
      } catch (error) {
        show(error.message || 'Operação não concluída.', true);
        return null;
      } finally {
        busy = false;
      }
    }
    function edit(item) {
      editing = item.id;
      for (const name of ['name', 'host', 'port', 'protocol', 'username']) {
        setValue(name === 'name' ? 'name' : name === 'username' ? 'user' : name, String(item[name] ?? ''));
      }
      // Existing passwords stay server-side unless explicitly replaced.
      password.value = '';
      password.placeholder = item.hasPassword ? 'Deixe vazio para manter a senha atual' : 'Senha do proxy';
      mode.textContent = 'Editar proxy';
      saveButton.textContent = 'Salvar alterações ↗';
      show('Edite os dados e salve. A senha atual será mantida se ficar vazia.');
      view.querySelector('#proxy-name').focus();
    }
    function render() {
      count.textContent = list.length + (list.length === 1 ? ' registro' : ' registros');
      table.replaceChildren();
      const active = list.find(p => p.id === activeId);
      activeLabel.textContent = active ? 'Padrão selecionado: ' + active.name : 'Nenhum proxy ativo';
      disableActive.disabled = !active;
      if (list.length === 0) {
        const row = document.createElement('tr');
        const cell = document.createElement('td');
        cell.colSpan = 5;
        cell.className = 'proxy-empty-cell';
        cell.textContent = 'Nenhum proxy salvo. Cadastre o primeiro acima.';
        row.appendChild(cell);
        table.appendChild(row);
        return;
      }
      for (const item of list) {
        const row = document.createElement('tr');
        row.className = 'proxy-record-row';
        for (const txt of [
          item.name, item.host + ':' + item.port, item.protocol.toUpperCase(),
          item.id === activeId ? 'ATIVO' : 'Salvo',
        ]) {
          const td = document.createElement('td');
          td.textContent = txt;
          row.appendChild(td);
        }
        const td = document.createElement('td');
        td.className = 'proxy-record-actions';
        td.append(
          actionButton(item.id === activeId ? 'Em uso' : 'Usar', () =>
            invoke('/api/proxies/activate', {id: item.id}, 'Proxy padrão selecionado. Reinicie os navegadores para aplicar de forma consistente.'),
            item.id === activeId),
          actionButton('Editar', () => edit(item)),
          actionButton('Testar TCP', async () => {
            const response = await invoke('/api/proxies/test', {id: item.id});
            if (response) show(response.message, !response.reachable);
          }),
          actionButton('Remover', () => {
            if (window.confirm('Remover este proxy salvo?')) {
              invoke('/api/proxies/delete', {id: item.id}, 'Proxy removido.');
              if (editing === item.id) resetForm();
            }
          }),
        );
        row.appendChild(td);
        table.appendChild(row);
      }
    }
    async function refresh(force = false) {
      if (!force && Date.now() - lastFetch < 3000) return;
      try {
        const response = await api.get('/api/proxies');
        list = Array.isArray(response.proxies) ? response.proxies : [];
        activeId = response.activeId || null;
        lastFetch = Date.now();
        render();
      } catch (error) {
        show('Não foi possível consultar proxies: ' + error.message, true);
      }
    }
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const data = {
        name: value('name'), host: value('host'), port: value('port'),
        protocol: value('protocol'), username: value('user'),
        password: password.value,
      };
      if (editing) data.id = editing;
      const result = await invoke('/api/proxies/save', data, 'Proxy salvo no agente local.');
      if (result) resetForm();
    });
    toggle.addEventListener('click', () => {
      const reveal = password.type === 'password';
      password.type = reveal ? 'text' : 'password';
      toggle.setAttribute('aria-pressed', String(reveal));
      toggle.setAttribute('aria-label', reveal ? 'Ocultar senha' : 'Mostrar senha');
      toggle.title = reveal ? 'Ocultar senha' : 'Mostrar senha';
    });
    disableActive.addEventListener('click', () =>
      invoke('/api/proxies/activate', {id: null}, 'Proxy desativado para novas janelas.')
    );
    return {node: view, onShow: () => refresh()};
  }
  Object.defineProperty(window, 'NexoraProxyView', {
    value: Object.freeze({create}), writable: false, configurable: false,
  });
})();
