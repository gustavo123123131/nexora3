/* Renders the existing Python CustomTkinter widget tree, without duplicating its logic. */
'use strict';
const app = document.getElementById('application');
const nodes = new Map();
const selectedTabs = new Map();
const pendingInput = new Map();
const shownDialogs = new Set();
let currentState = null;
let hadSuccessfulConnection = false;
let disconnectedAfterSuccess = false;
let lastStateString = '';
let lastServerVersion = null;

function notify(message) {
  const slot = document.getElementById('notifications');
  const el = document.createElement('div');
  el.className = 'notification';
  el.textContent = message;
  slot.appendChild(el);
  setTimeout(() => el.remove(), 5000);
}
async function post(path, body) {
  const res = await fetch(path, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    credentials: 'same-origin', body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
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
      if (!tabs.some(tab => tab.id === active)) active = tabs[0]?.id;
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
      patchChildren(bar, buttons);
      const activeTab = tabs.find(tab => tab.id === active);
      const content = el.querySelector('.tab-content');
      patchChildren(content, activeTab ? [renderNode(activeTab)] : []);
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
    const response = await fetch('/api/state', {cache:'no-store'});
    if (!response.ok) throw Error(String(response.status));
    const data = await response.json();
    if (disconnectedAfterSuccess || (lastServerVersion && lastServerVersion !== data.server_version)) {
      location.reload(); // Python/CSS/JS hot reload.
      return;
    }
    lastServerVersion = data.server_version;
    hadSuccessfulConnection = true;
    const serialized = JSON.stringify(data.tree);
    currentState = data;
    if (serialized !== lastStateString) {
      lastStateString = serialized;
      updateTree(data.tree);
    }
    await handleDialogs(data.dialogs);
  } catch (e) {
    if (hadSuccessfulConnection) disconnectedAfterSuccess = true;
    else if (!currentState) app.textContent = 'Aguardando o servidor local…';
  } finally {
    setTimeout(poll, 550);
  }
}
poll();
