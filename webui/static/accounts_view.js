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
    const check = document.createElement('button');
    check.type = 'button';
    check.className = 'accounts-details';
    check.textContent = 'Verificar perfis';
    top.append(header, count, check);
    const recovery = document.createElement('div');
    recovery.className = 'accounts-recovery';
    recovery.hidden = true;
    check.addEventListener('click', async () => {
      if (!recovery.hidden) { recovery.hidden = true; return; }
      try {
        const integrity = await get('/api/local-profiles/integrity');
        const backup = await get('/api/local-profiles/recovery');
        recovery.replaceChildren();
        const info = document.createElement('p');
        info.textContent = 'Pastas ausentes: ' + integrity.missing.length +
          ' · Pastas sem conta: ' + integrity.orphanFolders.length +
          '. Nenhum arquivo foi movido ou alterado.';
        recovery.append(info);
        if (integrity.recoveryNotice) {
          const note = document.createElement('p');
          note.textContent = integrity.recoveryNotice;
          recovery.append(note);
        }
        for (const profile of (backup.candidates || []).slice(0, 30)) {
          const line = document.createElement('div');
          line.className = 'accounts-recovery-row';
          const description = document.createElement('span');
          description.textContent = profile.label + ' — ' + profile.accountId;
          const restore = document.createElement('button');
          restore.type = 'button';
          restore.className = 'accounts-details';
          restore.textContent = 'Restaurar cartão';
          restore.addEventListener('click', async () => {
            if (!window.confirm('Recuperar metadados de ' + profile.label +
                ' pelo backup? Não altera cookies nem substitui arquivos Chromium.')) return;
            try {
              await post('/api/local-profiles/restore', {
                accountId: profile.accountId, confirmed: true
              });
              recovery.hidden = true;
              status.textContent = profile.label + ': registro recuperado do backup.';
              await refresh();
            } catch (error) {
              status.textContent = 'Recuperação indisponível: ' + error.message;
            }
          });
          line.append(description, restore);
          recovery.append(line);
        }
        recovery.hidden = false;
      } catch (error) {
        status.textContent = 'Falha na verificação dos perfis: ' + error.message;
      }
    });
    const status = document.createElement('p');
    status.className = 'accounts-status';
    status.setAttribute('role', 'status');
    const list = document.createElement('div');
    list.className = 'accounts-local-grid';
    const selection = new Set();
    // Hide confirmed deletions immediately while the agent safely backs up
    // Chromium files. Restore the cards if the server refuses the deletion.
    const deleting = new Set();
    let latestProfiles = [];
    const toolbar = document.createElement('div');
    toolbar.className = 'accounts-selection';
    const allLabel = document.createElement('label');
    allLabel.className = 'accounts-select-all';
    const allBox = document.createElement('input');
    allBox.type = 'checkbox';
    const allText = document.createElement('span');
    allText.textContent = 'Selecionar todas';
    allLabel.append(allBox, allText);
    const selectedCount = document.createElement('span');
    selectedCount.className = 'accounts-count-selected';
    const removeSelected = document.createElement('button');
    removeSelected.type = 'button';
    removeSelected.className = 'accounts-delete';
    removeSelected.textContent = 'Excluir selecionadas';
    removeSelected.disabled = true;
    const selectable = new Set();
    function updateSelection() {
      for (const id of [...selection]) if (!selectable.has(id)) selection.delete(id);
      selectedCount.textContent = selection.size + ' selecionada(s)';
      removeSelected.disabled = selection.size === 0;
      allBox.checked = !!selectable.size && selection.size === selectable.size;
      allBox.indeterminate = !!selection.size && selection.size < selectable.size;
    }
    allBox.addEventListener('change', () => {
      selection.clear();
      if (allBox.checked) for (const id of selectable) selection.add(id);
      for (const checkbox of list.querySelectorAll('input[data-account-id]')) {
        checkbox.checked = selection.has(Number(checkbox.dataset.accountId));
      }
      updateSelection();
    });
    removeSelected.addEventListener('click', async () => {
      const ids = [...selection].filter(id => !deleting.has(id));
      if (!ids.length) return;
      // The button itself is the deletion request: do not stack dialogs.
      for (const id of ids) deleting.add(id);
      status.textContent = 'Excluindo ' + ids.length +
        ' conta(s): criando backups completos antes de remover os arquivos. Aguarde.';
      render(latestProfiles);
      try {
        const result = await post('/api/local-profiles/delete-batch', {
          ids, confirmed: true
        });
        const removed = result.deleted || [];
        const errors = result.errors || [];
        for (const item of removed) selection.delete(item.profileId);
        for (const id of ids) deleting.delete(id);
        await refresh();
        status.textContent = removed.length + ' conta(s) excluída(s) com backup. ' +
          (errors.length ? errors.length + ' não excluída(s): ' +
            errors.map(x => 'Conta ' + x.id + ' (' + x.reason + ')').join('; ') : '');
      } catch (error) {
        for (const id of ids) deleting.delete(id);
        render(latestProfiles);
        await refresh();
        status.textContent = 'Falha na exclusão em lote: ' + error.message;
      } finally {
        updateSelection();
      }
    });
    toolbar.append(allLabel, selectedCount, removeSelected);
    // Keep account cards and deleted-account backups in the SAME top-level
    // Contas tab. Reuse the existing backup renderer and restore endpoints.
    const sectionTabs = document.createElement('nav');
    sectionTabs.className = 'accounts-inner-tabs';
    sectionTabs.setAttribute('aria-label', 'Seções de Contas');
    const accountsTab = document.createElement('button');
    accountsTab.type = 'button';
    accountsTab.textContent = 'Contas';
    const backupTab = document.createElement('button');
    backupTab.type = 'button';
    backupTab.textContent = 'Backup';
    sectionTabs.append(accountsTab, backupTab);

    const accountsPanel = document.createElement('div');
    accountsPanel.className = 'accounts-current-panel';
    accountsPanel.append(top, status, toolbar, recovery, list);
    const backupPanel = document.createElement('div');
    backupPanel.className = 'accounts-backup-panel';
    backupPanel.hidden = true;
    const backupView = window.NexoraBackupView.create({
      get, post, onRestore: () => refresh()
    });
    backupPanel.append(backupView.node);
    let showingBackups = false;
    let generation = 0;
    let lastSnapshot = null;
    function showSection(backups) {
      showingBackups = backups;
      accountsPanel.hidden = backups;
      backupPanel.hidden = !backups;
      accountsTab.classList.toggle('active', !backups);
      backupTab.classList.toggle('active', backups);
      accountsTab.setAttribute('aria-pressed', String(!backups));
      backupTab.setAttribute('aria-pressed', String(backups));
      if (backups) backupView.onShow();
      else refresh();
    }
    accountsTab.addEventListener('click', () => showSection(false));
    backupTab.addEventListener('click', () => showSection(true));
    node.append(sectionTabs, accountsPanel, backupPanel);
    showSection(false);

    const labels = {
      pending: 'Pendente', confirming: 'Confirmando cadastro',
      created: 'Criada', error: 'Erro',
      rejected: 'Recusada', verification_required: 'Verificação necessária',
      unconfirmed: 'Não confirmada'
    };
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
      latestProfiles = profiles;
      const visible = profiles.filter(p => !deleting.has(p.id));
      selectable.clear();
      for (const p of visible) selectable.add(p.id);
      updateSelection();
      list.replaceChildren();
      count.textContent = visible.length + (visible.length === 1 ? ' conta' : ' contas');
      if (!visible.length) {
        const empty = document.createElement('p');
        empty.className = 'accounts-empty';
        empty.textContent = 'Nenhuma conta criada ainda. Use Abrir Navegadores para criar uma.';
        list.append(empty);
        return;
      }
      for (const profile of visible) {
        const savedDevice = profile.hasRuntimeConfig === true;
        // Account creation status reflects *only* site registration events.
        // Older records lacking device snapshots are not failed sign-ups.
        const currentStatus = Object.prototype.hasOwnProperty.call(labels, profile.status)
          ? profile.status : 'pending';
        const card = document.createElement('article');
        card.className = 'accounts-local-card';
        const head = document.createElement('header');
        const name = document.createElement('h2');
        name.textContent = profile.label || 'Conta';
        const badge = document.createElement('span');
        badge.className = 'accounts-status-label accounts-status-' + currentStatus;
        badge.textContent = labels[currentStatus];
        const selectLabel = document.createElement('label');
        selectLabel.className = 'accounts-select-one';
        const selectBox = document.createElement('input');
        selectBox.type = 'checkbox';
        selectBox.dataset.accountId = String(profile.id);
        selectBox.checked = selection.has(profile.id);
        selectBox.setAttribute('aria-label', 'Selecionar ' + (profile.label || 'conta'));
        selectBox.addEventListener('change', () => {
          if (selectBox.checked) selection.add(profile.id);
          else selection.delete(profile.id);
          updateSelection();
        });
        selectLabel.append(selectBox, name);
        head.append(selectLabel, badge);

        const detail = document.createElement('div');
        detail.className = 'accounts-summary';
        detail.append(
          textRow('Username', profile.username),
          textRow('E-mail', profile.email),
          textRow('ID da plataforma', profile.platformUserId ||
            (profile.platformObservedUserId
              ? profile.platformObservedUserId + ' (observado, não verificado)'
              : 'Não identificado')),
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
          // One click triggers the request. Backup is created by the agent
          // before it removes this account; a failed request restores the card.
          deleting.add(profile.id);
          selection.delete(profile.id);
          status.textContent = accountName +
            ': exclusão em andamento. Criando backup completo antes de apagar os arquivos.';
          render(latestProfiles);
          try {
            const result = await post('/api/local-profiles/delete', {
              id: profile.id, confirmed: true, removeFiles: true
            });
            if (result.ok !== true || result.profileId !== profile.id) {
              throw new Error('O agente não confirmou a exclusão.');
            }
            deleting.delete(profile.id);
            await refresh();
            status.textContent = result.legacyFilesPreserved
              ? accountName + ' excluída da lista e salva em Contas > Backup; pasta Chromium legada preservada.'
              : accountName + ' excluída, com backup em Contas > Backup.';
          } catch (error) {
            deleting.delete(profile.id);
            render(latestProfiles);
            await refresh();
            status.textContent = 'Não foi possível excluir ' + accountName +
              ': ' + error.message + '. O cartão foi restaurado.';
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
              textRow('Status do cadastro', labels[stored.status] || 'Não confirmada'),
              textRow('ID da plataforma', stored.platformUserId ||
                (stored.platformObservedUserId
                  ? stored.platformObservedUserId + ' (observado, não verificado)'
                  : 'Não identificado')),
              textRow('Estado da identificação',
                stored.platformUserId ? 'Verificado' :
                stored.platformObservedUserId ? 'Somente observado; autenticação não confirmada' :
                'Não identificado'),
              textRow('Fonte observada', stored.platformObservedSource || 'Nenhuma'),
              textRow('Origem do ID remoto', stored.platformIdSource || 'Não comprovada'),
              textRow('ID verificado em', stored.platformIdVerifiedAt || 'Não verificado'),
              textRow('Fonte da confirmação',
                stored.registrationClickedAt && stored.registrationReviewRequired
                  ? 'Clique em Registrar (sem confirmação do site)'
                  : stored.registrationReviewRequired
                    ? 'Confirmação antiga — revisão necessária'
                    : (stored.registrationConfirmationSource || 'Não comprovada')),
              textRow('UUID da conta', stored.accountId),
              textRow(
                stored.deviceProfileIdOrigin === 'migration-internal-only'
                  ? 'UUID interno (migração)' : 'UUID da emulação',
                stored.deviceProfileId
              ),
              textRow('Diretório Chromium', stored.profilePath),
              textRow('Sessão', stored.sessionStatus || 'closed'),
              textRow('Última abertura', stored.lastOpenedAt),
              textRow('Proxy', stored.proxyId ? 'Vinculada' : (stored.connectionMode === 'managed' ? 'Configuração vinculada' : 'Não vinculada'))
            );
            expanded.append(
              textRow('Username', stored.username || 'Não informado'),
              textRow('Nome real do titular (financeiro)', stored.financialRealName || 'Não confirmado'),
              textRow('CPF financeiro', stored.financialCpf || 'Não confirmado'),
              textRow('Senha da conta', stored.hasLoginPassword ? 'Protegida; requer autorização para mostrar' : 'Indisponível'),
              textRow('PIN de saque', stored.hasWithdrawPin ? 'Protegido no cofre Windows' : 'Não configurado no Nexora')
            );
            const identityForm = document.createElement('section');
            identityForm.className = 'accounts-financial-form';
            const financialTitle = document.createElement('h3');
            financialTitle.textContent = 'Identidade financeira desta conta';
            const owner = document.createElement('input');
            owner.placeholder = 'Nome real completo registrado na plataforma';
            owner.setAttribute('aria-label', 'Nome real completo do titular');
            owner.autocomplete = 'off';
            owner.value = stored.financialRealName || '';
            const cpf = document.createElement('input');
            cpf.placeholder = 'CPF real do titular';
            cpf.setAttribute('aria-label', 'CPF real do titular');
            cpf.inputMode = 'numeric';
            cpf.autocomplete = 'off';
            cpf.value = stored.financialCpf || '';
            const acknowledged = document.createElement('label');
            const consent = document.createElement('input');
            consent.type = 'checkbox';
            acknowledged.append(consent, document.createTextNode(' Confirmo que estes dados são do titular real da conta'));
            const feedback = document.createElement('p');
            feedback.setAttribute('role', 'status');
            const saveOwner = document.createElement('button');
            saveOwner.type = 'button';
            saveOwner.className = 'accounts-details';
            saveOwner.textContent = 'Salvar titular financeiro';
            saveOwner.addEventListener('click', async () => {
              if (!consent.checked) { feedback.textContent = 'Confirme a titularidade.'; return; }
              saveOwner.disabled = true;
              try {
                await post('/api/local-profiles/financial-identity', {
                  profileId: stored.id, accountId: stored.accountId,
                  realName: owner.value, cpf: cpf.value,
                  ownershipAcknowledged: true
                });
                feedback.textContent = 'Titular atualizado nesta conta. Não constitui validação externa da plataforma.';
                consent.checked = false;
              } catch (error) {
                feedback.textContent = 'Não foi possível salvar: ' + error.message;
              } finally { saveOwner.disabled = false; }
            });
            const pin = document.createElement('input');
            pin.type = 'password';
            pin.inputMode = 'numeric';
            pin.maxLength = 6;
            pin.autocomplete = 'new-password';
            pin.placeholder = 'PIN de saque existente (6 números)';
            pin.setAttribute('aria-label', 'PIN de saque desta conta');
            const useTestPin = document.createElement('button');
            useTestPin.type = 'button';
            useTestPin.className = 'accounts-details';
            useTestPin.textContent = 'Usar PIN de saque de teste (387520)';
            useTestPin.addEventListener('click', () => {
              // Explicit operator choice. Does NOT change login or provider PIN.
              pin.value = '387520';
              feedback.textContent = 'PIN de teste preenchido somente para saque. Salve apenas se já for o PIN desta conta na plataforma.';
            });
            const savePin = document.createElement('button');
            savePin.type = 'button';
            savePin.className = 'accounts-details';
            savePin.textContent = 'Salvar PIN no cofre Windows';
            savePin.addEventListener('click', async () => {
              savePin.disabled = true;
              try {
                await post('/api/local-profiles/withdraw-pin', {
                  profileId: stored.id, accountId: stored.accountId, pin: pin.value
                });
                feedback.textContent = 'PIN de saque protegido separadamente da senha de login.';
              } catch (error) {
                feedback.textContent = 'Não foi possível salvar o PIN.';
              } finally { pin.value = ''; savePin.disabled = false; }
            });
            const reveal = document.createElement('button');
            reveal.type = 'button';
            reveal.className = 'accounts-details';
            reveal.textContent = 'Mostrar senha (administrador)';
            reveal.addEventListener('click', async () => {
              const authorization = window.prompt('Chave local de autorização para visualizar a senha de login:');
              if (!authorization) return;
              reveal.disabled = true;
              try {
                const result = await post('/api/local-profiles/reveal-login', {
                  profileId: stored.id, accountId: stored.accountId, authorization
                });
                const value = document.createElement('input');
                value.type = 'password';
                value.readOnly = true;
                value.value = result.password || '';
                value.setAttribute('aria-label', 'Senha da conta protegida');
                const toggle = document.createElement('button');
                toggle.type = 'button';
                toggle.textContent = 'Mostrar / ocultar';
                toggle.onclick = () => { value.type = value.type === 'password' ? 'text' : 'password'; };
                const close = document.createElement('button');
                close.type = 'button';
                close.textContent = 'Ocultar e limpar';
                close.onclick = () => { value.value = ''; value.remove(); toggle.remove(); close.remove(); };
                identityForm.append(value, toggle, close);
              } catch (error) {
                feedback.textContent = 'Credencial não autorizada ou indisponível.';
              } finally { reveal.disabled = false; }
            });
            identityForm.append(financialTitle, owner, cpf, acknowledged, saveOwner,
              pin, useTestPin, savePin, reveal, feedback);
            expanded.append(identityForm);
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
    async function refresh() {
      const current = ++generation;
      try {
        const data = await get('/api/local-profiles');
        if (current !== generation) return;
        const profiles = Array.isArray(data.profiles) ? data.profiles : [];
        const snapshot = JSON.stringify(profiles);
        // Do not rebuild cards when nothing changed: keep expanded details,
        // selection and scrolling intact during background status checks.
        if (snapshot !== lastSnapshot) {
          lastSnapshot = snapshot;
          render(profiles);
        }
      } catch (error) {
        if (current === generation) {
          status.textContent = 'Não foi possível carregar as contas: ' + error.message;
        }
      }
    }
    // Live status updates only while the accounts view is mounted/visible.
    // The API returns the persisted status; no optimistic "Criada" labels.
    setInterval(() => {
      if (node.isConnected && !showingBackups && !document.hidden) {
        refresh();
      }
    }, 1500);
    return {node, onShow: () => showingBackups ? backupView.onShow() : refresh()};
  }
  Object.defineProperty(window, 'NexoraAccountsView', {
    value: Object.freeze({create}), writable: false, configurable: false,
  });
})();
