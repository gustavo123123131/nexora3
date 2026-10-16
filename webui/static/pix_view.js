/* Local-only Pix key manager. Values are rendered as text, never HTML. */
'use strict';
(function () {
  const labels = {CPF: 'CPF', PHONE: 'Telefone', EMAIL: 'E-mail', EVP: 'Aleatória'};
  const ddds = new Set([
    11,12,13,14,15,16,17,18,19,21,22,24,27,28,
    31,32,33,34,35,37,38,41,42,43,44,45,46,47,48,49,
    51,53,54,55,61,62,63,64,65,66,67,68,69,
    71,73,74,75,77,79,81,82,83,84,85,86,87,88,89,
    91,92,93,94,95,96,97,98,99
  ]);
  const emailPattern = /^[a-z0-9.!#$'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$/;
  const evpPattern = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;

  function identify(value) {
    const found = {};
    if (typeof value !== 'string' || value.length > 128 || !value.trim() ||
        [...value].some(ch => ch.charCodeAt(0) < 32 || ch.charCodeAt(0) === 127)) return found;
    const raw = value.trim();
    if (/^(?:\d{11}|\d{3}\.\d{3}\.\d{3}-\d{2})$/.test(raw)) {
      const cpf = raw.replace(/\D/g, '');
      if (cpf.length === 11 && !/^(\d)\1+$/.test(cpf)) {
        const ok = [9, 10].every(length => {
          const sum = Array.from({length}, (_, i) => Number(cpf[i]) * (length + 1 - i))
            .reduce((a, b) => a + b, 0);
          const digit = sum * 10 % 11;
          return Number(cpf[length]) === (digit === 10 ? 0 : digit);
        });
        if (ok) found.CPF = cpf;
      }
    }
    if (/^\+?[0-9 ()\-.]+$/.test(raw)) {
      let phone = raw.replace(/[ ()\-.]/g, '');
      if (phone.startsWith('+')) phone = phone.startsWith('+55') ? phone.slice(3) : '';
      else if (phone.length === 13 && phone.startsWith('55')) phone = phone.slice(2);
      if (/^\d{11}$/.test(phone) && ddds.has(Number(phone.slice(0, 2))) &&
          phone[2] === '9' && !/^(\d)\1+$/.test(phone.slice(2))) {
        // Chaves de telefone são exibidas com DDD + 9 dígitos, sem +55.
        found.PHONE = phone;
      }
    }
    const email = raw.toLowerCase();
    if (email.length <= 77 && emailPattern.test(email)) {
      const [local, domain] = email.split('@');
      if (local.length <= 64 && domain.includes('.') && /\.[a-z]{2,63}$/.test(domain)) {
        found.EMAIL = email;
      }
    }
    if (evpPattern.test(raw.toLowerCase())) found.EVP = raw.toLowerCase();
    return found;
  }

  // During an automatic Windows-agent update, the browser may briefly read
  // older E.164 records. Keep the UI consistent without changing their IDs.
  function displayKey(item) {
    const value = String(item.value || '');
    return item.type === 'PHONE' && /^\+55\d{11}$/.test(value)
      ? value.slice(3) : value;
  }

  function create(api) {
    const node = document.createElement('section');
    node.className = 'pix-client-view';
    const wrap = document.createElement('div');
    wrap.className = 'pix-simple';
    const title = document.createElement('h1');
    title.textContent = 'Gerenciamento de Chaves Pix';
    const description = document.createElement('p');
    description.textContent = 'Salve chaves somente neste dispositivo. A validação verifica o formato, não confirma o vínculo no banco.';
    const addButton = document.createElement('button');
    addButton.className = 'pix-add-button';
    addButton.type = 'button';
    addButton.textContent = 'Adicionar Chave Pix';
    const form = document.createElement('form');
    form.className = 'pix-simple-form';
    form.hidden = true;
    const field = document.createElement('input');
    field.type = 'text';
    field.placeholder = 'CPF, celular com DDD (11 dígitos), e-mail ou UUID';
    field.autocomplete = 'off';
    field.spellcheck = false;
    field.maxLength = 128;
    field.setAttribute('aria-label', 'Chave Pix');
    const save = document.createElement('button');
    save.type = 'submit';
    save.textContent = 'Salvar';
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'pix-cancel';
    cancel.textContent = 'Cancelar';
    const recognition = document.createElement('div');
    recognition.className = 'pix-recognition';
    recognition.setAttribute('aria-live', 'polite');
    const choose = document.createElement('select');
    choose.setAttribute('aria-label', 'Escolher tipo da chave Pix');
    choose.hidden = true;
    const feedback = document.createElement('p');
    feedback.className = 'pix-simple-feedback';
    feedback.setAttribute('role', 'status');
    const list = document.createElement('div');
    list.className = 'pix-simple-list';
    form.append(field, save, cancel);
    wrap.append(title, description, addButton, form, recognition, choose, feedback, list);
    node.append(wrap);
    let busy = false;
    let editing = null;
    let items = [];
    let generation = 0;

    function message(value, error = false) {
      feedback.textContent = value;
      feedback.classList.toggle('pix-error', error);
    }

    function checkValue() {
      const options = identify(field.value);
      const kinds = Object.keys(options);
      const previouslySelected = choose.value;
      choose.replaceChildren();
      choose.hidden = kinds.length <= 1 || form.hidden;
      if (kinds.length === 0) {
        recognition.textContent = field.value.trim() ? 'Formato inválido.' : '';
      } else if (kinds.length === 1) {
        recognition.textContent = 'Tipo identificado: ' + labels[kinds[0]];
      } else {
        recognition.textContent = 'A chave pode ser CPF ou Telefone. Escolha o tipo:';
        const blank = document.createElement('option');
        blank.value = '';
        blank.textContent = 'Escolher tipo';
        choose.append(blank);
        for (const kind of kinds) {
          const option = document.createElement('option');
          option.value = kind;
          option.textContent = labels[kind];
          choose.append(option);
        }
        // A saved account's type is known when opening the editor.
        if (editing && field.value.trim() === displayKey(editing)) {
          choose.value = editing.type;
        } else if (kinds.includes(previouslySelected)) {
          choose.value = previouslySelected;
        }
      }
      return options;
    }

    function hideForm() {
      form.hidden = true;
      editing = null;
      field.value = '';
      recognition.textContent = '';
      choose.hidden = true;
      addButton.hidden = false;
    }

    function showForm(item) {
      if (busy) return;
      editing = item || null;
      field.value = item ? displayKey(item) : '';
      save.textContent = item ? 'Salvar alteração' : 'Salvar';
      form.hidden = false;
      addButton.hidden = true;
      message('');
      checkValue();
      field.focus();
    }

    function render() {
      list.replaceChildren();
      if (!items.length) {
        const empty = document.createElement('p');
        empty.className = 'pix-simple-empty';
        empty.textContent = 'Nenhuma chave Pix cadastrada.';
        list.append(empty);
        return;
      }
      for (const item of items) {
        const row = document.createElement('div');
        row.className = 'pix-simple-row';
        const info = document.createElement('div');
        info.className = 'pix-simple-info';
        const kind = document.createElement('small');
        kind.textContent = labels[item.type] || item.type;
        const value = document.createElement('code');
        value.textContent = displayKey(item);
        info.append(kind, value);
        const actions = document.createElement('div');
        actions.className = 'pix-row-actions';
        const edit = document.createElement('button');
        edit.type = 'button';
        edit.textContent = 'Editar';
        edit.addEventListener('click', () => showForm(item));
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'pix-remove';
        remove.textContent = 'X';
        remove.setAttribute('aria-label', 'Excluir chave Pix ' + (items.indexOf(item) + 1));
        remove.addEventListener('click', async () => {
          if (busy) return;
          busy = true;
          remove.disabled = true;
          try {
            const result = await api.post('/api/pix-keys/delete', {id: item.id});
            if (result.ok !== true || result.id !== item.id) throw new Error('Exclusão não confirmada.');
            items = items.filter(record => record.id !== item.id);
            if (editing && editing.id === item.id) hideForm();
            render();
            message('Chave Pix excluída.');
          } catch (error) {
            message('Não foi possível excluir: ' + error.message, true);
          } finally {
            busy = false;
            remove.disabled = false;
          }
        });
        actions.append(edit, remove);
        row.append(info, actions);
        list.append(row);
      }
    }

    async function refresh() {
      const current = ++generation;
      try {
        const data = await api.get('/api/pix-keys');
        if (current !== generation) return;
        items = Array.isArray(data.keys) ? data.keys : [];
        render();
      } catch (error) {
        if (current === generation) message('Falha ao carregar as chaves: ' + error.message, true);
      }
    }

    addButton.addEventListener('click', () => showForm(null));
    cancel.addEventListener('click', hideForm);
    field.addEventListener('input', () => {
      // A new value must not silently inherit an earlier ambiguous choice.
      choose.value = '';
      checkValue();
    });
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (busy) return;
      const options = checkValue();
      const types = Object.keys(options);
      if (!types.length) {
        message('Chave inválida. Confira o formato informado.', true);
        return;
      }
      const type = types.length === 1 ? types[0] : choose.value;
      if (!type || !options[type]) {
        message('Escolha CPF ou Telefone antes de salvar.', true);
        return;
      }
      busy = true;
      save.disabled = true;
      try {
        const path = editing ? '/api/pix-keys/update' : '/api/pix-keys/add';
        const payload = {value: field.value.trim(), type};
        if (editing) payload.id = editing.id;
        const result = await api.post(path, payload);
        if (!result.key || !result.key.id) throw new Error('Salvamento não confirmado.');
        const wasEditing = !!editing;
        hideForm();
        await refresh();
        message(wasEditing ? 'Chave Pix atualizada.' : 'Chave Pix cadastrada.');
      } catch (error) {
        message('Não foi possível salvar: ' + error.message, true);
      } finally {
        busy = false;
        save.disabled = false;
      }
    });

    return {node, onShow: refresh};
  }

  Object.defineProperty(window, 'NexoraPixView', {
    value: Object.freeze({create}), writable: false, configurable: false
  });
})();
