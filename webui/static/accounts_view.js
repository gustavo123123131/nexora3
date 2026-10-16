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
        const savedDevice = profile.hasRuntimeConfig === true;
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
          textRow('Tipo', profile.origin === 'mae' ? 'Mãe' : 'Filha'),
          textRow('Telefone', profile.phone),
          textRow('Dispositivo', deviceName(profile)),
          textRow('Criada em', profile.createdAt ? new Date(profile.createdAt).toLocaleDateString('pt-BR') : '')
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
          if (!window.confirm('Excluir o cartão de ' + accountName +
              '? Uma cópia de segurança dos dados da lista será preservada.')) return;
          const removeFiles = window.confirm(
            'Apagar TAMBÉM os cookies, histórico e arquivos Chromium desta conta? ' +
            'OK = apagar arquivos. Cancelar = conservar os arquivos.'
          );
          open.disabled = true;
          remove.disabled = true;
          try {
            await post('/api/local-profiles/delete', {
              id: profile.id, confirmed: true, removeFiles
            });
            status.textContent = accountName + ' excluída com sucesso.';
            await refresh();
          } catch (error) {
            status.textContent = 'Não foi possível excluir ' + accountName + ': ' + error.message;
            remove.disabled = false;
            open.disabled = !savedDevice;
          }
        });
        const view = document.createElement('button');
        view.type = 'button';
        view.className = 'accounts-details';
        view.textContent = 'Detalhes';
        const expanded = document.createElement('div');
        expanded.className = 'accounts-expanded';
        expanded.hidden = true;
        view.addEventListener('click', async () => {
          if (!expanded.hidden) {
            expanded.hidden = true;
            view.textContent = 'Detalhes';
            return;
          }
          try {
            const stored = await get('/api/local-profiles/detail?id=' + encodeURIComponent(profile.id));
            expanded.replaceChildren();
            expanded.append(
              textRow('Nome', stored.fullName),
              textRow('UUID da conta', stored.accountId),
              textRow('UUID da emulação', stored.deviceProfileId),
              textRow('Diretório Chromium', stored.profilePath),
              textRow('Sessão', stored.sessionStatus || 'closed'),
              textRow('Última abertura', stored.lastOpenedAt),
              textRow('Proxy', stored.proxyId ? 'Vinculada' : (stored.connectionMode === 'managed' ? 'Configuração vinculada' : 'Não vinculada'))
            );
            if (stored.runtimeConfig) {
              expanded.append(textRow('User-Agent salvo', stored.runtimeConfig.user_agent));
              expanded.append(textRow('Viewport', JSON.stringify(stored.runtimeConfig.viewport)));
            }
            for (const entry of (stored.history || []).slice(-8).reverse()) {
              expanded.append(textRow('Histórico', (entry.at || '') + ' — ' + (entry.event || '')));
            }
            expanded.hidden = false;
            view.textContent = 'Ocultar';
          } catch (error) {
            status.textContent = 'Falha ao carregar detalhes: ' + error.message;
          }
        });
        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'accounts-close';
        close.textContent = 'Fechar';
        close.addEventListener('click', async () => {
          try {
            const result = await post('/api/local-profiles/close', {id: profile.id});
            status.textContent = (profile.label || 'Conta') + ': ' + result.message;
          } catch (error) {
            status.textContent = 'Falha ao fechar conta: ' + error.message;
          }
        });
        footer.append(view, open, close, remove);

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
          card.append(head, detail, explanation, expanded, footer);
        } else {
          card.append(head, detail, expanded, footer);
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
