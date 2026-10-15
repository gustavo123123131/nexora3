/* Renders the existing Python CustomTkinter widget tree, without duplicating its logic. */
'use strict';
const app = document.getElementById('application');
const nodes = new Map();
const selectedTabs = new Map();
const pendingInput = new Map();
const shownDialogs = new Set();
// Proxy workspace uses local-agent endpoints and does not change existing GUI callbacks.
const PROXY_PREVIEW_ID = '__nexora_proxy_visual__';
let proxyPreviewNode = null;
let proxyPreviewInstance = null;
function proxyPreview() {
  if (!proxyPreviewInstance) {
    proxyPreviewInstance = window.NexoraProxyView.create({
      get: getRegistration,
      post: post,
    });
    proxyPreviewNode = proxyPreviewInstance.node;
  }
  proxyPreviewInstance.onShow();
  return proxyPreviewNode;
}
let currentState = null;
let hadSuccessfulConnection = false;
let disconnectedAfterSuccess = false;
let lastStateString = '';
let lastServerVersion = null;
let csrfToken = null;
let connectionWarning = null;
function showConnectionWarning(message) {
  if (!connectionWarning) {
    connectionWarning = document.createElement('div');
    connectionWarning.id = 'connection-warning';
    connectionWarning.setAttribute('role', 'status');
    document.body.appendChild(connectionWarning);
  }
  connectionWarning.textContent = message;
  connectionWarning.hidden = !message;
}

// The Codespaces page may remain open in a tab after its backend stops.
// Checking the cloud *separately* from the Windows agent avoids incorrectly
// reporting an agent failure as a Codespaces failure.
let cloudServerVersion = null;
let cloudWasOffline = false;
async function checkCloudServer() {
  if (!runningInCodespaces) return;
  try {
    const response = await fetch('/api/health', {cache: 'no-store'});
    if (!response.ok) throw Error(String(response.status));
    const status = await response.json();
    if (status.service !== 'lixocpa') throw Error('Serviço inesperado');
    if (cloudWasOffline || (cloudServerVersion && cloudServerVersion !== status.server_version)) {
      location.reload();
      return;
    }
    cloudServerVersion = status.server_version;
    cloudWasOffline = false;
    showConnectionWarning('');
  } catch (_) {
    cloudWasOffline = true;
    showConnectionWarning('Servidor do Codespaces indisponível. Reabra o Codespace e execute: bash .devcontainer/start.sh');
  } finally {
    setTimeout(checkCloudServer, 3500);
  }
}

// On a GitHub Codespaces link, the UI runs remotely but browser actions
// go to the user's Windows-only companion, never to the cloud Playwright.
const runningInCodespaces = location.hostname.endsWith('.app.github.dev');
if (runningInCodespaces) checkCloudServer();
const localAgentBase = 'http://127.0.0.1:8767';
let agentKey = runningInCodespaces ? (sessionStorage.getItem('lixocpa-agent-key') || '') : '';
let agentConnected = false;
let pairingScreen = null;

async function fetchAgentState(key) {
  return fetch(localAgentBase + '/api/state', {
    method: 'GET', mode: 'cors', credentials: 'omit', cache: 'no-store',
    headers: {'X-LixoCPA-Agent-Key': key},
  });
}

function showAgentPairing(message = '') {
  if (!runningInCodespaces) return;
  if (pairingScreen && pairingScreen.isConnected) {
    pairingScreen.querySelector('.agent-error').textContent = message;
    return;
  }
  nodes.clear();
  selectedTabs.clear();
  lastStateString = '';
  currentState = null;
  pairingScreen = document.createElement('div');
  pairingScreen.className = 'agent-setup';
  const title = document.createElement('h2');
  title.textContent = 'Conectar Chromium do Windows';
  const description = document.createElement('p');
  description.textContent =
    'Este painel está no GitHub. Para abrir janelas na sua área de trabalho, '
    + 'execute o agente no Windows e cole abaixo o código exibido no terminal dele.';
  const box = document.createElement('div');
  box.className = 'agent-connect-form';
  const field = document.createElement('input');
  field.placeholder = 'Código de pareamento do agente Windows';
  field.type = 'password';
  field.autocomplete = 'off';
  const submit = document.createElement('button');
  submit.textContent = 'Conectar ao meu Windows';
  const error = document.createElement('p');
  error.className = 'agent-error';
  error.textContent = message;
  const fallback = document.createElement('p');
  fallback.textContent =
    'Se o navegador bloquear o acesso local, abra o painel diretamente em:';
  const link = document.createElement('a');
  link.href = localAgentBase;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  link.textContent = 'http://127.0.0.1:8767';
  const hint = document.createElement('p');
  hint.textContent =
    'O site remoto nunca recebe seu código; a conexão é feita pelo próprio navegador '
    + 'com o agente que escuta somente em 127.0.0.1.';
  hint.className = 'agent-note';
  box.append(field, submit);
  pairingScreen.append(title, description, box, error, fallback, link, hint);
  app.replaceChildren(pairingScreen);

  const connect = async () => {
    const key = field.value.trim();
    if (!key) { error.textContent = 'Digite o código mostrado no agente Windows.'; return; }
    submit.disabled = true;
    error.textContent = 'Conectando ao agente local...';
    try {
      const response = await fetchAgentState(key);
      if (!response.ok) throw Error('Código inválido ou agente indisponível (HTTP ' + response.status + ').');
      const data = await response.json();
      if (!data.tree || !data.csrf_token) throw Error('Resposta inválida do agente.');
      agentKey = key;
      sessionStorage.setItem('lixocpa-agent-key', key);
      agentConnected = true;
      pairingScreen = null;
      csrfToken = data.csrf_token;
      lastServerVersion = data.server_version;
      currentState = data;
      app.replaceChildren();
      updateTree(data.tree);
      lastStateString = JSON.stringify(data.tree);
      notify('Agente Windows conectado. O Chromium abrirá no seu computador.');
    } catch (exc) {
      error.textContent =
        'Não foi possível conectar: ' + exc.message
        + ' Verifique se o agente está aberto e se a porta 8767 está livre. '
        + 'Se o navegador bloquear a conexão, use o link local acima.';
    } finally { submit.disabled = false; }
  };
  submit.addEventListener('click', connect);
  field.addEventListener('keydown', event => {
    if (event.key === 'Enter') connect();
  });
}


function notify(message) {
  const slot = document.getElementById('notifications');
  const el = document.createElement('div');
  el.className = 'notification';
  el.textContent = message;
  slot.appendChild(el);
  setTimeout(() => el.remove(), 5000);
}
async function post(path, body) {
  if (runningInCodespaces && !agentConnected) throw Error('Agente Windows não conectado.');
  const headers = {'Content-Type': 'application/json', 'X-LixoCPA-CSRF': csrfToken || ''};
  if (runningInCodespaces) headers['X-LixoCPA-Agent-Key'] = agentKey;
  const res = await fetch((runningInCodespaces ? localAgentBase : '') + path, {
    method: 'POST', headers,
    mode: 'cors', credentials: runningInCodespaces ? 'omit' : 'same-origin',
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    // Include FastAPI's safe error description instead of an opaque 403.
    let detail = '';
    try {
      const response = await res.json();
      if (typeof response?.detail === 'string') detail = response.detail;
    } catch (_) {}
    throw new Error(`HTTP ${res.status}${detail ? ': ' + detail : ''}`);
  }
  return res.json();
}
async function getRegistration(path) {
  if (runningInCodespaces && !agentConnected) {
    throw Error('Conecte o agente Windows para acessar suas contas.');
  }
  const headers = {};
  if (runningInCodespaces) headers['X-LixoCPA-Agent-Key'] = agentKey;
  const res = await fetch((runningInCodespaces ? localAgentBase : '') + path, {
    method: 'GET', mode: 'cors',
    credentials: runningInCodespaces ? 'omit' : 'same-origin',
    cache: 'no-store', headers,
  });
  if (!res.ok) throw new Error('Erro ao consultar contas (HTTP ' + res.status + ').');
  return res.json();
}
async function send(id, event, value = null) {
  try { await post('/api/event', {id, event, value}); }
  catch (err) { notify(`Falha no comando: ${err.message}`); }
}
function delayChange(id, value) {
  const pending = pendingInput.get(id);
  if (pending) clearTimeout(pending.timer);
  pendingInput.set(id, {value, timer: setTimeout(() => flushChange(id), 110)});
}
async function flushChange(id) {
  const item = pendingInput.get(id);
  if (!item) return;
  clearTimeout(item.timer);
  pendingInput.delete(id);
  await send(id, 'change', item.value);
}
async function flushAllChanges() {
  await Promise.all([...pendingInput.keys()].map(flushChange));
}
function spanValue(x, first) {
  if (Array.isArray(x)) return `${Number(x[first ? 0 : 1] || 0)}px`;
  return `${Number(x || 0)}px`;
}
function cssLayout(el, obj) {
  const layout = obj.layout || {};
  el.style.marginLeft = spanValue(layout.padx, true);
  el.style.marginRight = spanValue(layout.padx, false);
  el.style.marginTop = spanValue(layout.pady, true);
  el.style.marginBottom = spanValue(layout.pady, false);
  el.style.display = obj.hidden ? 'none' : '';
  el.style.flex = layout.expand ? '1 1 auto' : '';
  el.style.alignSelf = (layout.fill === 'x' || layout.fill === 'both') ? 'stretch' : '';
  if (layout.method === 'grid') {
    el.style.gridColumn = `${Number(layout.column || 0) + 1} / span ${Number(layout.columnspan || 1)}`;
    el.style.gridRow = `${Number(layout.row || 0) + 1} / span ${Number(layout.rowspan || 1)}`;
    el.style.justifySelf = layout.sticky?.includes('ew') || (layout.sticky?.includes('w') && layout.sticky?.includes('e')) ? 'stretch' : (layout.sticky?.includes('w') ? 'start' : (layout.sticky?.includes('e') ? 'end' : 'stretch'));
  } else {
    el.style.gridColumn = '';
    el.style.gridRow = '';
  }
}
function renderContainerLayout(el, obj) {
  if (!obj.children?.length) return;
  const children = obj.children.filter(ch => !ch.hidden);
  const gridded = children.some(ch => ch.layout?.method === 'grid');
  if (gridded) {
    el.classList.add('grid-layout');
    el.classList.remove('row-layout', 'column-layout');
    const maxColumn = Math.max(0, ...children.map(ch => (ch.layout?.column || 0) + (ch.layout?.columnspan || 1) - 1));
    const maxRow = Math.max(0, ...children.map(ch => (ch.layout?.row || 0) + (ch.layout?.rowspan || 1) - 1));
    el.style.gridTemplateColumns = Array.from({length: maxColumn + 1}, (_, i) => `${Math.max(1, Number(obj.cols?.[i] || 1))}fr`).join(' ');
    el.style.gridTemplateRows = Array.from({length: maxRow + 1}, (_, i) => obj.rows?.[i] ? 'minmax(auto, 1fr)' : 'auto').join(' ');
  } else {
    el.classList.remove('grid-layout');
    const sides = children.filter(ch => ['left','right'].includes(ch.layout?.side));
    el.classList.toggle('row-layout', sides.length > 0);
    el.classList.toggle('column-layout', sides.length === 0);
    el.style.gridTemplateColumns = '';
    el.style.gridTemplateRows = '';
  }
}
function createNode(obj) {
  let tag = 'div';
  if (obj.type === 'button') tag = 'button';
  else if (obj.type === 'entry') tag = 'input';
  else if (obj.type === 'option') tag = 'select';
  else if (obj.type === 'textbox') tag = 'textarea';
  else if (obj.type === 'label') tag = 'div';
  else if (['checkbox', 'switch'].includes(obj.type)) tag = 'label';
  const el = document.createElement(tag);
  el.dataset.id = obj.id;
  el.classList.add('widget', `ctk-${obj.type}`);
  if (obj.type === 'entry') {
    el.addEventListener('input', e => delayChange(obj.id, e.currentTarget.value));
  }
  if (obj.type === 'button') {
    el.addEventListener('click', async () => { await flushAllChanges(); await send(obj.id, 'click'); });
    el.addEventListener('contextmenu', async e => {
      e.preventDefault(); await flushAllChanges(); await send(obj.id, 'contextmenu');
    });
  }
  if (['checkbox', 'switch'].includes(obj.type)) {
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.addEventListener('change', async () => {
      await flushAllChanges(); await send(obj.id, 'change', checkbox.checked);
    });
    const caption = document.createElement('span');
    caption.className = 'check-label';
    el.append(checkbox, caption);
  }
  if (obj.type === 'option') {
    el.addEventListener('change', async e => { await flushAllChanges(); await send(obj.id, 'change', e.target.value); });
  }
  if (obj.type === 'textbox') el.readOnly = true;
  if (obj.type === 'tabs') {
    const bar = document.createElement('div');
    bar.className = 'tab-bar';
    const content = document.createElement('div');
    content.className = 'tab-content';
    el.append(bar, content);
  }
  nodes.set(obj.id, el);
  return el;
}
function setControlledValue(el, value) {
  const v = String(value ?? '');
  if (document.activeElement !== el && el.value !== v) el.value = v;
}
function updateOptions(el, obj) {
  const o = obj.options || {};
  cssLayout(el, obj);
  if (o.width) el.style.width = `${o.width}px`;
  if (o.height && !['label','entry','button','switch','checkbox'].includes(obj.type)) el.style.minHeight = `${o.height}px`;
  if (o.corner_radius != null) el.style.borderRadius = `${o.corner_radius}px`;
  if (o.fg_color && o.fg_color !== 'transparent') el.style.backgroundColor = o.fg_color;
  if (o.fg_color === 'transparent') el.style.backgroundColor = 'transparent';
  if (o.text_color) el.style.color = o.text_color;
  if (o.font) {
    if (o.font.size) el.style.fontSize = `${o.font.size}px`;
    if (o.font.weight) el.style.fontWeight = o.font.weight;
  }
  if (['button','label'].includes(obj.type)) {
    const txt = String(o.text ?? '');
    if (el.textContent !== txt) el.textContent = txt;
  }
  if (obj.type === 'label' && o.justify === 'left') el.style.textAlign = 'left';
  if (obj.type === 'entry') {
    el.type = o.show ? 'password' : 'text';
    el.disabled = o.state === 'disabled';
    setControlledValue(el, o.value);
  }
  if (obj.type === 'button') el.disabled = o.state === 'disabled';
  if (['checkbox','switch'].includes(obj.type)) {
    const c = el.querySelector('input');
    c.checked = Boolean(o.value);
    c.disabled = o.state === 'disabled';
    const label = el.querySelector('.check-label');
    if (label.textContent !== String(o.text || '')) label.textContent = String(o.text || '');
  }
  if (obj.type === 'option') {
    const choices = o.values || [];
    if (el.dataset.choices !== JSON.stringify(choices)) {
      el.replaceChildren(...choices.map(choice => {
        const option = document.createElement('option');
        option.value = String(choice);
        option.textContent = String(choice);
        return option;
      }));
      el.dataset.choices = JSON.stringify(choices);
    }
    el.disabled = o.state === 'disabled';
    setControlledValue(el, o.value);
  }
  if (obj.type === 'textbox') {
    if (el.value !== String(o.value ?? '')) {
      const follow = el.scrollTop + el.clientHeight >= el.scrollHeight - 40;
      el.value = String(o.value ?? '');
      if (follow) el.scrollTop = el.scrollHeight;
    }
  }
  if (obj.type === 'modal') {
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-modal', 'true');
  }
  // Presentation-only roles; events, widget IDs and API behavior are unchanged.
  if (obj.type === 'button') {
    const label = String(o.text || '');
    const appearance = {
      'Iniciar (COM automação)': 'ui-action-start',
      'Iniciar (SEM automação)': 'ui-action-manual',
      'Abrir Navegadores': 'ui-action-start',
      'Reabrir guias': 'ui-action-reopen',
      'PARAR': 'ui-action-stop',
    };
    for (const token of ['ui-action-start', 'ui-action-manual', 'ui-action-reopen', 'ui-action-stop', 'ui-action-utility']) {
      el.classList.toggle(token, appearance[label] === token ||
        (token === 'ui-action-utility' && !appearance[label]));
    }
  }
  if (obj.type === 'label') {
    const label = String(o.text || '');
    el.classList.toggle('ui-section-title',
      ['Criar Mãe', 'Criar Filha', 'Configurações', 'Ações', 'Logs ao vivo'].includes(label));
    el.classList.toggle('ui-status',
      /^(Pronto\.?|Executando\.{0,3}|Parando\.{0,3}|Reabrindo guias\.{0,3}|Configurações salvas\.?|Proxy Ligado|Proxy Desativado)$/.test(label));
  }
}
function patchChildren(host, newChildren) {
  const old = [...host.children];
  if (old.length === newChildren.length && old.every((node, i) => node === newChildren[i])) return;
  // Reorder by stable id; preserve input DOM nodes and typing focus.
  newChildren.forEach((child, i) => {
    if (host.children[i] !== child) host.insertBefore(child, host.children[i] || null);
  });
  for (const item of [...host.children]) {
    if (!newChildren.includes(item)) item.remove();
  }
}
function renderNode(obj) {
  const el = nodes.get(obj.id) || createNode(obj);
  updateOptions(el, obj);
  if (['tabs','frame','root','scroll','modal'].includes(obj.type)) {
    if (obj.type === 'tabs') {
      const tabs = obj.children || [];
      let active = selectedTabs.get(obj.id);
      if (active !== PROXY_PREVIEW_ID && !tabs.some(tab => tab.id === active)) active = tabs[0]?.id;
      selectedTabs.set(obj.id, active);
      const bar = el.querySelector('.tab-bar');
      const oldButtons = new Map([...bar.children].map(button => [button.dataset.tabid, button]));
      const buttons = tabs.map(tab => {
        let b = oldButtons.get(tab.id);
        if (!b) {
          b = document.createElement('button');
          b.type = 'button';
          b.dataset.tabid = tab.id;
          b.addEventListener('click', () => {
            selectedTabs.set(obj.id, tab.id);
            if (currentState) updateTree(currentState.tree);
          });
        }
        b.textContent = tab.options?._tab_label || 'Aba';
        b.classList.toggle('active', tab.id === active);
        return b;
      });
      // Append a frontend-only third item; existing Mãe/Filha navigation is
      // unchanged, as are the backend tab IDs and callback bindings.
      let proxyButton = oldButtons.get(PROXY_PREVIEW_ID);
      if (!proxyButton) {
        proxyButton = document.createElement('button');
        proxyButton.type = 'button';
        proxyButton.dataset.tabid = PROXY_PREVIEW_ID;
        proxyButton.className = 'proxy-tab';
        proxyButton.addEventListener('click', () => {
          selectedTabs.set(obj.id, PROXY_PREVIEW_ID);
          if (currentState) updateTree(currentState.tree);
        });
      }
      proxyButton.textContent = 'Proxy';
      proxyButton.classList.toggle('active', active === PROXY_PREVIEW_ID);
      proxyButton.setAttribute('aria-current', active === PROXY_PREVIEW_ID ? 'page' : 'false');
      patchChildren(bar, [...buttons, proxyButton]);
      const activeTab = tabs.find(tab => tab.id === active);
      el.dataset.activeView = active === PROXY_PREVIEW_ID
        ? 'Proxy' : (activeTab?.options?._tab_label || '');
      const content = el.querySelector('.tab-content');
      patchChildren(content, active === PROXY_PREVIEW_ID
        ? [proxyPreview()] : (activeTab ? [renderNode(activeTab)] : []));
    } else {
      renderContainerLayout(el, obj);
      const children = obj.children || [];
      const wanted = children.filter(ch => ch.type !== 'modal').map(renderNode);
      patchChildren(el, wanted);
      children.filter(ch => ch.type === 'modal').forEach(ch => {
        const modal = renderNode(ch);
        if (modal.parentElement !== document.body) document.body.appendChild(modal);
      });
    }
  }
  return el;
}
function allIds(obj, into) {
  into.add(obj.id);
  (obj.children || []).forEach(child => allIds(child, into));
}
function updateTree(root) {
  const node = renderNode(root);
  patchChildren(app, [node]);
  const found = new Set();
  allIds(root, found);
  for (const [id, el] of nodes) {
    if (!found.has(id)) {
      el.remove();
      nodes.delete(id);
      selectedTabs.delete(id);
    }
  }
}
async function handleDialogs(dialogs) {
  for (const d of dialogs || []) {
    if (shownDialogs.has(d.id)) continue;
    shownDialogs.add(d.id);
    let value;
    if (d.kind === 'askstring') value = window.prompt(`${d.title}\n\n${d.message}`, d.initialvalue || '');
    else if (d.kind === 'askyesno') value = window.confirm(`${d.title}\n\n${d.message}`);
    else { window.alert(`${d.title}\n\n${d.message}`); value = true; }
    try { await post('/api/dialog', {id: d.id, value}); }
    catch (err) { notify('A janela de confirmação expirou.'); }
  }
}
async function poll() {
  try {
    if (runningInCodespaces && !agentKey) {
      showAgentPairing();
      return;
    }
    const response = runningInCodespaces
      ? await fetchAgentState(agentKey)
      : await fetch('/api/state', {cache:'no-store'});
    if (runningInCodespaces && response.status === 403) {
      agentKey = '';
      sessionStorage.removeItem('lixocpa-agent-key');
      agentConnected = false;
      showAgentPairing('O agente foi reiniciado ou o código expirou. Insira o código atual.');
      return;
    }
    if (!response.ok) throw Error(String(response.status));
    const data = await response.json();
    if (disconnectedAfterSuccess || (lastServerVersion && lastServerVersion !== data.server_version)) {
      location.reload(); // Python/CSS/JS hot reload.
      return;
    }
    lastServerVersion = data.server_version;
    hadSuccessfulConnection = true;
    if (!runningInCodespaces) showConnectionWarning('');
    if (runningInCodespaces) agentConnected = true;
    const serialized = JSON.stringify(data.tree);
    csrfToken = data.csrf_token;
    currentState = data;
    if (serialized !== lastStateString) {
      lastStateString = serialized;
      updateTree(data.tree);
    }
    await handleDialogs(data.dialogs);
  } catch (e) {
    if (runningInCodespaces) {
      agentConnected = false;
      // A remote site cannot guarantee local-network browser permission.
      if (!pairingScreen) {
        agentKey = '';
        sessionStorage.removeItem('lixocpa-agent-key');
        showAgentPairing('Conexão com o Windows interrompida. Confira o agente e conecte novamente.');
      }
    } else {
      if (hadSuccessfulConnection) disconnectedAfterSuccess = true;
      else if (!currentState) app.textContent = 'Servidor local indisponível.';
      showConnectionWarning('Servidor localhost fora do ar. Execute o comando de inicialização e aguarde a reconexão automática.');
    }
  } finally {
    setTimeout(poll, 550);
  }
}
poll();
