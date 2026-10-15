/* Minimal Nexora Proxy list; all requests go to the existing paired local agent. */
'use strict';
(function () {
  function create(api) {
    const view = document.createElement('section');
    view.className = 'proxy-client-view';
    const wrap = document.createElement('div');
    wrap.className = 'proxy-simple';
    const title = document.createElement('h1');
    title.textContent = 'Proxy';
    const description = document.createElement('p');
    description.textContent = 'Cole uma proxy por linha. Uma conexão é atribuída a cada nova sessão. O IP de uma proxy rotativa depende do provedor.';
    const form = document.createElement('form');
    form.className = 'proxy-simple-form';
    const field = document.createElement('input');
    field.type = 'text';
    field.placeholder = 'host:porta:usuario:senha ou protocolo://usuario:senha@host:porta';
    field.autocomplete = 'off';
    field.spellcheck = false;
    field.setAttribute('aria-label', 'Linha completa da proxy');
    const add = document.createElement('button');
    add.type = 'submit';
    add.textContent = 'Adicionar Proxy';
    const feedback = document.createElement('p');
    feedback.className = 'proxy-simple-feedback';
    feedback.setAttribute('role', 'status');
    const list = document.createElement('div');
    list.className = 'proxy-simple-list';
    form.append(field, add);
    wrap.append(title, description, form, feedback, list);
    view.append(wrap);
    let busy = false;
    function message(text, failed) {
      feedback.textContent = text;
      feedback.classList.toggle('proxy-error', !!failed);
    }
    function readable(item) {
      const address = item.host + ':' + item.port;
      const prefix = item.username
        ? encodeURIComponent(item.username) + (item.hasPassword ? ':********' : '') + '@'
        : '';
      return item.protocol + '://' + prefix + address;
    }
    async function refresh() {
      try {
        const result = await api.get('/api/proxies');
        const items = Array.isArray(result.proxies) ? result.proxies : [];
        list.replaceChildren();
        if (!items.length) {
          const empty = document.createElement('p');
          empty.textContent = 'Nenhuma proxy adicionada. Sem proxy configurada, a conexão será direta.';
          empty.className = 'proxy-simple-empty';
          list.append(empty);
          return;
        }
        for (const item of items) {
          const row = document.createElement('div');
          row.className = 'proxy-simple-row';
          const info = document.createElement('div');
          info.className = 'proxy-simple-info';
          const line = document.createElement('code');
          line.textContent = readable(item);
          line.title = 'Senha armazenada localmente e ocultada nesta lista.';
          const type = document.createElement('small');
          type.textContent = item.protocol.toUpperCase();
          info.append(line, type);
          const remove = document.createElement('button');
          remove.type = 'button';
          remove.textContent = 'Excluir';
          remove.addEventListener('click', async () => {
            if (busy) return;
            busy = true;
            remove.disabled = true;
            try {
              await api.post('/api/proxies/delete', {id: item.id});
              message('Proxy excluída.');
              await refresh();
            } catch (error) {
              message(error.message || 'Falha ao excluir proxy.', true);
            } finally {
              busy = false;
              remove.disabled = false;
            }
          });
          row.append(info, remove);
          list.append(row);
        }
      } catch (error) {
        message('Não foi possível carregar proxies: ' + error.message, true);
      }
    }
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (busy) return;
      const line = field.value.trim();
      if (!line) {
        message('Cole uma linha de proxy antes de adicionar.', true);
        return;
      }
      busy = true;
      add.disabled = true;
      try {
        await api.post('/api/proxies/add-line', {line});
        field.value = '';
        message('Proxy salva. Ela será usada nas próximas sessões.');
        await refresh();
      } catch (error) {
        message(error.message || 'Linha de proxy inválida.', true);
      } finally {
        busy = false;
        add.disabled = false;
      }
    });
    return {node: view, onShow: refresh};
  }
  Object.defineProperty(window, 'NexoraProxyView', {
    value: Object.freeze({create}), writable: false, configurable: false,
  });
})();
