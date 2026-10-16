/* Persistent live log console shared by every Nexora workspace tab. */
'use strict';
(function () {
  function create({get, isReady}) {
    const panel = document.createElement('aside');
    panel.id = 'global-logs';
    panel.setAttribute('aria-label', 'Painel global de logs');
    const header = document.createElement('div');
    header.className = 'global-logs-header';
    const title = document.createElement('strong');
    title.textContent = 'Logs ao vivo';
    const indicator = document.createElement('span');
    indicator.className = 'global-logs-indicator';
    indicator.textContent = 'Aguardando agente';
    header.append(title, indicator);
    const consoleBox = document.createElement('div');
    consoleBox.className = 'global-logs-console';
    consoleBox.setAttribute('role', 'log');
    consoleBox.setAttribute('aria-label', 'Eventos de Mãe e Filha em ordem de chegada');
    consoleBox.setAttribute('aria-live', 'off');
    const body = document.createElement('pre');
    body.className = 'global-logs-text';
    const logText = document.createTextNode('');
    body.append(logText);
    consoleBox.append(body);
    const footer = document.createElement('div');
    footer.className = 'global-logs-footer';
    const count = document.createElement('span');
    count.textContent = '0 eventos';
    const jump = document.createElement('button');
    jump.type = 'button';
    jump.className = 'global-logs-jump';
    jump.textContent = 'Ir ao fim';
    footer.append(count, jump);
    panel.append(header, consoleBox, footer);

    let cursor = 0;
    let entries = 0;
    let running = false;
    let follow = true;
    let shownGap = false;
    consoleBox.addEventListener('scroll', () => {
      follow = consoleBox.scrollHeight - consoleBox.scrollTop - consoleBox.clientHeight < 64;
      jump.hidden = follow;
    });
    jump.hidden = true;
    jump.addEventListener('click', () => {
      consoleBox.scrollTop = consoleBox.scrollHeight;
      follow = true;
      jump.hidden = true;
    });

    function append(text) {
      logText.appendData(text);
      if (follow) consoleBox.scrollTop = consoleBox.scrollHeight;
    }

    async function poll() {
      if (!running) return;
      let delay = 750;
      if (!isReady()) {
        indicator.textContent = 'Aguardando agente';
        window.setTimeout(poll, 900);
        return;
      }
      try {
        const data = await get('/api/live-logs?after=' + cursor + '&limit=500');
        if (!data || !Array.isArray(data.events)) throw Error('Resposta inválida');
        if (data.truncated && !shownGap) {
          append('[Sistema] Histórico anterior não está mais disponível nesta sessão.\n');
          shownGap = true;
        }
        let text = '';
        for (const item of data.events) {
          if (typeof item.seq !== 'number' || item.seq <= cursor) continue;
          const date = new Date(item.at);
          const clock = Number.isNaN(date.valueOf())
            ? '--:--:--' : date.toLocaleTimeString('pt-BR', {hour12: false});
          const source = item.source === 'Mãe' || item.source === 'Filha'
            ? item.source : 'Sistema';
          text += '[' + clock + '] [' + source + '] ' + String(item.text ?? '') + '\n';
          cursor = item.seq;
          entries += 1;
        }
        if (text) append(text);
        if (typeof data.cursor === 'number' && data.cursor > cursor) cursor = data.cursor;
        count.textContent = entries + (entries === 1 ? ' evento' : ' eventos');
        indicator.textContent = 'Ao vivo';
        indicator.classList.add('connected');
        if (data.hasMore) delay = 60;
      } catch (_) {
        indicator.textContent = 'Reconectando';
        indicator.classList.remove('connected');
        delay = 1200;
      }
      window.setTimeout(poll, delay);
    }

    return {
      node: panel,
      start() {
        if (running) return;
        running = true;
        poll();
      },
    };
  }
  Object.defineProperty(window, 'NexoraLiveLogs', {
    value: Object.freeze({create}), writable: false, configurable: false,
  });
})();
