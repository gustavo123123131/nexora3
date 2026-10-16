/* Restore deleted accounts on the LOCAL Windows agent. Never load cookie data into DOM. */
'use strict';
(function () {
  function create({get, post, onRestore = null}) {
    const node = document.createElement('section');
    node.className = 'accounts-local backup-local';
    const heading = document.createElement('header');
    heading.className = 'accounts-page-heading';
    const headingText = document.createElement('div');
    const title = document.createElement('h1');
    title.textContent = 'Backup';
    const subtitle = document.createElement('p');
    subtitle.textContent = 'Contas excluídas, dados Chromium e cookies disponíveis para restauração.';
    headingText.append(title, subtitle);
    const refreshButton = document.createElement('button');
    refreshButton.type = 'button';
    refreshButton.className = 'accounts-details';
    refreshButton.textContent = 'Atualizar';
    heading.append(headingText, refreshButton);
    const message = document.createElement('p');
    message.className = 'accounts-status';
    message.setAttribute('role', 'status');
    const list = document.createElement('div');
    list.className = 'accounts-local-grid';
    node.append(heading, message, list);

    const row = (name, value) => {
      const holder = document.createElement('div');
      holder.className = 'account-summary-row';
      const label = document.createElement('span');
      label.className = 'account-summary-key';
      label.textContent = name;
      const text = document.createElement('span');
      text.className = 'account-summary-value';
      text.textContent = value == null ? '—' : String(value);
      holder.append(label, text);
      return holder;
    };
    async function refresh() {
      try {
        const result = await get('/api/account-backups');
        const snapshots = Array.isArray(result.backups) ? result.backups : [];
        list.replaceChildren();
        if (!snapshots.length) {
          const empty = document.createElement('p');
          empty.className = 'accounts-empty';
          empty.textContent = 'Nenhuma conta foi excluída com backup completo ainda.';
          list.append(empty);
          return;
        }
        for (const saved of snapshots) {
          const card = document.createElement('article');
          card.className = 'accounts-local-card';
          const header = document.createElement('header');
          const name = document.createElement('h2');
          name.textContent = saved.label || 'Conta salva';
          const badge = document.createElement('span');
          badge.className = 'accounts-status-label accounts-status-' + (saved.restored ? 'created' : 'pending');
          badge.textContent = saved.restored ? 'Restaurada' : 'Disponível';
          header.append(name, badge);
          const detail = document.createElement('div');
          detail.className = 'accounts-summary';
          detail.append(
            row('UUID', saved.accountId),
            row('Dispositivo', saved.device),
            row('Arquivos Chromium', saved.hasBrowserData ? saved.fileCount + ' salvos' : 'Não disponíveis'),
            row('Salvo em', saved.createdAt ? new Date(saved.createdAt).toLocaleString('pt-BR') : '')
          );
          const extra = document.createElement('div');
          extra.className = 'accounts-expanded';
          extra.hidden = true;
          const footer = document.createElement('div');
          footer.className = 'accounts-card-footer';
          const inspect = document.createElement('button');
          inspect.type = 'button';
          inspect.className = 'accounts-details';
          inspect.textContent = 'Detalhes';
          inspect.addEventListener('click', async () => {
            if (!extra.hidden) {
              extra.hidden = true;
              inspect.textContent = 'Detalhes';
              return;
            }
            try {
              const record = await get('/api/account-backups/detail?backupId=' + encodeURIComponent(saved.id));
              extra.replaceChildren(
                row('Username', record.username),
                row('E-mail', record.email),
                row('Telefone', record.phone),
                row('Nome', record.fullName),
                row('UUID do dispositivo', record.deviceProfileId),
                row('Pasta original', record.profilePath),
                row('Status', record.statusMessage || record.status),
                row('Proxy', record.proxyId ? 'Vinculada' : 'Não vinculada')
              );
              extra.hidden = false;
              inspect.textContent = 'Ocultar detalhes';
            } catch (error) {
              message.textContent = 'Não foi possível visualizar: ' + error.message;
            }
          });
          const open = document.createElement('button');
          open.type = 'button';
          open.className = 'accounts-open';
          open.textContent = 'Restaurar';
          open.disabled = !!saved.restored;
          open.addEventListener('click', async () => {
            if (!window.confirm('Restaurar ' + saved.label + ' com a identidade e dados de navegador salvos?')) return;
            open.disabled = true;
            try {
              const answer = await post('/api/account-backups/restore', {
                backupId: saved.id, confirmed: true
              });
              message.textContent = saved.label + ' restaurada como Conta ' + answer.profileId + '.';
              await refresh();
              if (onRestore) await onRestore();
            } catch (error) {
              message.textContent = 'Não foi possível restaurar ' + saved.label + ': ' + error.message;
              open.disabled = false;
            }
          });
          footer.append(inspect, open);
          card.append(header, detail, extra, footer);
          list.append(card);
        }
      } catch (error) {
        message.textContent = 'Falha ao carregar backups: ' + error.message;
      }
    }
    refreshButton.addEventListener('click', refresh);
    return {node, onShow: refresh};
  }
  Object.defineProperty(window, 'NexoraBackupView', {
    value: Object.freeze({create}), writable: false, configurable: false,
  });
})();
