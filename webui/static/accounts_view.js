/* Nexora: minimal account cards. Never expose saved proxy credentials in this view. */
'use strict';
(function () {
  function create({get, post}) {
    const node = document.createElement('section');
    node.className = 'accounts-local';
    const top = document.createElement('header');
    top.className = 'accounts-page-heading';
    const header = document.createElement('div');
    const title = document.createElement('h1');
    title.textContent = 'Contas';
    const subtitle = document.createElement('p');
    subtitle.textContent = 'Cada conta mantém seu navegador, dispositivo e dados individuais.';
    header.append(title, subtitle);
    const count = document.createElement('span');
    count.className = 'accounts-count';
    top.append(header, count);
    const status = document.createElement('p');
    status.className = 'accounts-status';
    status.setAttribute('role', 'status');
    const list = document.createElement('div');
    list.className = 'accounts-local-grid';
    node.append(top, status, list);

    const labels = {pending: 'Pendente', created: 'Criada', error: 'Erro'};
    function textRow(label, value) {
      const row = document.createElement('div');
      row.className = 'account-summary-row';
      const key = document.createElement('span');
      key.className = 'account-summary-key';
      key.textContent = label;
      const val = document.createElement('span');
      val.className = 'account-summary-value';
      val.textContent = value || '—';
      val.title = value || '';
      row.append(key, val);
      return row;
    }
    function deviceName(profile) {
      if (profile.deviceLabel) return profile.deviceLabel;
      const modes = {android: 'Android', iphone: 'iPhone', desktop: 'Computador'};
      return modes[profile.deviceMode] || 'Não registrado';
    }
    function render(profiles) {
      list.replaceChildren();
      count.textContent = profiles.length + (profiles.length === 1 ? ' conta' : ' contas');
      if (!profiles.length) {
        const empty = document.createElement('p');
        empty.className = 'accounts-empty';
        empty.textContent = 'Nenhuma conta criada ainda. Use Abrir Navegadores para criar uma.';
        list.append(empty);
        return;
      }
      for (const profile of profiles) {
        const savedDevice = profile.runtimeConfig
          && profile.runtimeConfig.mode === profile.deviceMode
          && !!profile.runtimeConfig.user_agent;
        // Account creation status reflects *only* site registration events.
        // Older records lacking device snapshots are not failed sign-ups.
        const currentStatus = profile.status === 'created' ||
          profile.status === 'error' ? profile.status : 'pending';
        const card = document.createElement('article');
        card.className = 'accounts-local-card';
        const head = document.createElement('header');
        const name = document.createElement('h2');
        name.textContent = profile.label || 'Conta';
        const badge = document.createElement('span');
        badge.className = 'accounts-status-label accounts-status-' + currentStatus;
        badge.textContent = labels[currentStatus];
        head.append(name, badge);

        const detail = document.createElement('div');
        detail.className = 'accounts-summary';
        detail.append(
          textRow('Username', profile.username),
          textRow('E-mail', profile.email),
          textRow('Dispositivo', deviceName(profile))
        );

        const footer = document.createElement('div');
        footer.className = 'accounts-card-footer';
        const open = document.createElement('button');
        open.type = 'button';
        open.className = 'accounts-open';
        open.textContent = 'Abrir';
        if (!savedDevice) {
          open.disabled = true;
          open.title = 'Configuração técnica original não registrada. Não é seguro inventar outro dispositivo.';
        }
        open.addEventListener('click', async () => {
          if (open.disabled) return;
          open.disabled = true;
          try {
            await post('/api/local-profiles/open', {id: profile.id});
            status.textContent = 'Inicializando navegador da ' + (profile.label || 'conta') + '...';
            let confirmed = false;
            for (let n = 0; n < 36; n++) {
              await new Promise(resolve => setTimeout(resolve, 700));
              const state = await get('/api/local-profiles/open-state?id=' + encodeURIComponent(profile.id));
              if (state.state === 'ready') {
                status.textContent = profile.label + ': navegador aberto com o perfil salvo.';
                confirmed = true;
                break;
              }
              if (state.state === 'error' || state.state === 'closed') {
                status.textContent = profile.label + ': ' + state.message;
                confirmed = true;
                break;
              }
            }
            if (!confirmed) {
              status.textContent = profile.label + ': abertura ainda não confirmada. Consulte os logs.';
            }
          } catch (error) {
            status.textContent = 'Não foi possível abrir ' + profile.label + ': ' + error.message;
          } finally {
            open.disabled = false;
          }
        });
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'accounts-delete';
        remove.textContent = 'Excluir';
        remove.title = 'Excluir esta conta e seus dados locais de navegador';
        remove.addEventListener('click', async () => {
          const accountName = profile.label || 'esta conta';
          if (!window.confirm('Excluir ' + accountName +
              '? Os dados locais do navegador serão apagados. Esta ação não pode ser desfeita.')) return;
          open.disabled = true;
          remove.disabled = true;
          try {
            await post('/api/local-profiles/delete', {
              id: profile.id, confirmed: true
            });
            status.textContent = accountName + ' excluída com sucesso.';
            await refresh();
          } catch (error) {
            status.textContent = 'Não foi possível excluir ' + accountName + ': ' + error.message;
            remove.disabled = false;
            open.disabled = !savedDevice;
          }
        });
        footer.append(open, remove);

        // Lack of technical history prevents an *exact* reopen, but does
        // not provide evidence that external registration failed.
        const issue = !savedDevice
          ? 'Perfil antigo: dispositivo original não foi salvo. Reabertura indisponível; a conta continua preservada.'
          : (profile.statusMessage || '');
        if (issue) {
          const explanation = document.createElement('p');
          explanation.className = 'accounts-card-note';
          explanation.textContent = issue;
          explanation.title = issue;
          card.append(head, detail, explanation, footer);
        } else {
          card.append(head, detail, footer);
        }
        list.append(card);
      }
    }
    let generation = 0;
    async function refresh() {
      const current = ++generation;
      try {
        const data = await get('/api/local-profiles');
        if (current !== generation) return;
        render(Array.isArray(data.profiles) ? data.profiles : []);
      } catch (error) {
        if (current === generation) {
          status.textContent = 'Não foi possível carregar as contas: ' + error.message;
        }
      }
    }
    return {node, onShow: refresh};
  }
  Object.defineProperty(window, 'NexoraAccountsView', {
    value: Object.freeze({create}), writable: false, configurable: false,
  });
})();
