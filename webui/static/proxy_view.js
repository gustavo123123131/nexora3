/* Nexora / Proxy workspace.
 * Presentation only. No API requests, no persistence, no proxy connections.
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
        <div class="proxy-state-tag"><span class="state-dot"></span> INTEGRAÇÃO PENDENTE</div>
      </div>
      <div class="proxy-notice" role="status">
        <span class="proxy-notice-icon" aria-hidden="true">i</span>
        <div>
          <strong>Prévia da próxima etapa</strong>
          <p>Esta tela é somente visual. Nenhuma configuração é salva, nenhum proxy é conectado e os dados digitados não são enviados ao servidor.</p>
        </div>
      </div>
      <div class="proxy-layout">
        <section class="proxy-panel proxy-config" aria-labelledby="proxy-config-title">
          <header class="proxy-panel-heading">
            <div class="proxy-panel-icon" aria-hidden="true">01</div>
            <div><h2 id="proxy-config-title">Detalhes do proxy</h2><p>Prepare os dados para a integração futura.</p></div>
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
              <small id="proxy-protocol-hint">Opções previstas para a integração futura; nenhuma conexão é realizada.</small>
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
              <div><strong>Configuração não ativa</strong><span>A gravação será habilitada na etapa da API.</span></div>
              <button class="proxy-save-button" type="button" disabled title="Disponível após a integração com a API">Salvar proxy <span aria-hidden="true">↗</span></button>
            </div>
          </form>
        </section>
        <section class="proxy-panel proxy-directory" aria-labelledby="proxy-list-title">
          <header class="proxy-panel-heading">
            <div class="proxy-panel-icon" aria-hidden="true">02</div>
            <div><h2 id="proxy-list-title">Proxies cadastrados</h2><p>A lista será preenchida após a integração.</p></div>
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
                <p>Após a integração, os proxies adicionados aparecerão aqui, com seus estados reais.</p>
                <div class="proxy-inactive-actions" aria-label="Ações futuras indisponíveis">
                  <button disabled type="button" title="Disponível após cadastrar um proxy">Editar</button>
                  <button disabled type="button" title="Disponível após cadastrar um proxy">Remover</button>
                </div>
              </td></tr></tbody>
            </table>
          </div>
          <footer class="proxy-directory-footer"><span class="proxy-inactive-dot"></span> Status: não configurado <span class="proxy-directory-note">Nenhum proxy em uso nesta tela</span></footer>
        </section>
      </div>
    </div>
  `;
  function create() {
    const view = document.createElement('div');
    view.className = 'proxy-client-view';
    view.innerHTML = template; // Static developer-owned markup, no user data interpolation.
    const form = view.querySelector('#proxy-preview-form');
    form.addEventListener('submit', event => event.preventDefault());
    const password = view.querySelector('#proxy-password');
    const toggle = view.querySelector('.proxy-password-toggle');
    toggle.addEventListener('click', () => {
      const reveal = password.type === 'password';
      password.type = reveal ? 'text' : 'password';
      toggle.setAttribute('aria-pressed', String(reveal));
      toggle.setAttribute('aria-label', reveal ? 'Ocultar senha' : 'Mostrar senha');
      toggle.title = reveal ? 'Ocultar senha' : 'Mostrar senha';
    });
    return view;
  }
  Object.defineProperty(window, 'NexoraProxyView', {
    value: Object.freeze({create}), writable: false, configurable: false,
  });
})();
