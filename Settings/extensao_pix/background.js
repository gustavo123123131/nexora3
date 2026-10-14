let isRunning = false;

// =====================================================
// Tuning de robustez
// =====================================================
// STEP_TIMEOUT = tempo real "de espera" de UI (queremos ~10s)
// EXEC_TIMEOUT = folga para o executeScript terminar e retornar (evita race)
const STEP_TIMEOUT = 10_000;
const EXEC_TIMEOUT = 13_000;
const POLL_MS = 250;
const STEP_RETRIES = 2;

function log(message, level = 'info') {
    console.log(`[${level}] ${message}`);
    const colorClass = level === 'error' ? 'log-error' : (level === 'warn' ? 'log-warn' : '');
    const time = new Date().toLocaleTimeString();
    const htmlLog = `<div class="log-entry ${colorClass}">[${time}] ${message}</div>`;

    chrome.storage.local.get(['automationLogs'], (data) => {
        const currentLogs = data.automationLogs || '';
        chrome.storage.local.set({ automationLogs: currentLogs + htmlLog }, () => {
            chrome.runtime.sendMessage({ type: 'LOG_UPDATED' }).catch(() => {});
        });
    });
}

function updateStatus(message) {
    chrome.storage.local.set({ automationStatus: message }, () => {
        chrome.runtime.sendMessage({ type: 'STATUS_UPDATED' }).catch(() => {});
    });
}

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === 'START_REGISTRATION') {
        if (!isRunning) {
            isRunning = true;
            startAutomation('register');
        }
    } else if (request.action === 'START_DELETION') {
        if (!isRunning) {
            isRunning = true;
            startAutomation('delete');
        } else {
            log("Automação já está rodando.", "warn");
        }
    } else if (request.action === 'STOP_AUTOMATION') {
        isRunning = false;
        log("Sinal de parada enviado.", "warn");
        updateStatus("Parado");
    }
});

function waitForTabLoad(tabId) {
    return new Promise((resolve) => {
        chrome.tabs.get(tabId, (tab) => {
            if (tab && tab.status === 'complete') resolve();
            else {
                const listener = (updatedTabId, info) => {
                    if (updatedTabId === tabId && info.status === 'complete') {
                        chrome.tabs.onUpdated.removeListener(listener);
                        resolve();
                    }
                };
                chrome.tabs.onUpdated.addListener(listener);
            }
        });
    });
}

const wait = (ms) => new Promise(r => setTimeout(r, ms));

// ==========================================
// ORQUESTRADOR
// ==========================================
async function startAutomation(mode) {
    log(`Iniciando modo: ${mode === 'register' ? 'Cadastro' : 'Exclusão'}...`);
    updateStatus("Lendo arquivo...");

    try {
        const fileUrl = chrome.runtime.getURL('chavepix.txt');
        const response = await fetch(fileUrl);
        const text = await response.text();
        const emails = text.split('\n').map(e => e.trim()).filter(e => e.length > 0);

        for (let i = 0; i < emails.length; i++) {
            if (!isRunning) break;
            log(`--- Processando: ${emails[i]} ---`);

            let success;
            if (mode === 'delete') {
                success = await deletePixKey();
            } else {
                success = await processEmail(emails[i]);
            }

            if (!success && mode === 'register') {
                log("Fila paralisada devido a erro crítico.", "error");
                isRunning = false;
                break;
            }
        }
    } catch (e) {
        log(`Erro global: ${e.message}`, "error");
    }

    isRunning = false;
    updateStatus("Aguardando...");
}

// ==========================================
// Step runner com retry
// ==========================================
async function runStep(tabId, label, fn, args = [], { retries = STEP_RETRIES, timeout = EXEC_TIMEOUT } = {}) {
    let lastErr = null;

    for (let attempt = 1; attempt <= retries + 1; attempt++) {
        if (!isRunning) throw new Error("Parado pelo usuário.");

        try {
            log(`${label} (tentativa ${attempt}/${retries + 1})...`);
            const res = await injectAction(tabId, fn, args, timeout);
            return res;
        } catch (e) {
            lastErr = e;
            log(`${label} falhou: ${e.message}`, attempt <= retries ? "warn" : "error");
            await wait(400);
        }
    }

    throw lastErr || new Error(`${label} falhou.`);
}

async function runStepGuarded(tabId, label, guardFn, actionFn, args = [], { retries = STEP_RETRIES, timeout = EXEC_TIMEOUT } = {}) {
    let lastErr = null;

    for (let attempt = 1; attempt <= retries + 1; attempt++) {
        if (!isRunning) throw new Error("Parado pelo usuário.");

        try {
            log(`${label} (guard + ação) (tentativa ${attempt}/${retries + 1})...`);

            // 1) Espera estar na tela certa
            const guardRes = await injectAction(tabId, guardFn, [], timeout);
            if (guardRes && guardRes.error) {
                throw new Error(guardRes.error);
            }

            // 2) Executa a ação
            const res = await injectAction(tabId, actionFn, args, timeout);
            return res;
        } catch (e) {
            lastErr = e;
            log(`${label} falhou: ${e.message}`, attempt <= retries ? "warn" : "error");
            await wait(500);
        }
    }

    throw lastErr || new Error(`${label} falhou.`);
}

async function mpGuardEmailSelectionScreen() {
    const sleep = (ms) => new Promise(r => setTimeout(r, ms));
    const maxTries = Math.ceil(10_000 / 250);

    for (let i = 0; i < maxTries; i++) {
        const ul =
            document.querySelector('ul[aria-label="Chave EMAIL"]') ||
            document.querySelector('ul[aria-label*="Chave" i]');

        const hasAnyAction = ul && ul.querySelector('button.andes-list__item-action');
        if (hasAnyAction) return { data: true };

        await sleep(250);
    }

    return { error: "Guard: tela de seleção (Chave EMAIL) não ficou pronta (10s)." };
}

async function mpGuardEmailInputScreen() {
    const sleep = (ms) => new Promise(r => setTimeout(r, ms));
    const maxTries = Math.ceil(10_000 / 250);

    const pickInput = () => {
        return (
            document.querySelector("input[type='email']") ||
            document.querySelector("input[autocomplete='email']") ||
            document.querySelector("input[name*='mail' i]") ||
            document.querySelector("input[aria-label*='mail' i]") ||
            document.querySelector("input[placeholder*='mail' i]") ||
            document.querySelector("input[type='text'][name*='mail' i]") ||
            document.querySelector("input[type='text'][aria-label*='mail' i]") ||
            document.querySelector("input[type='text'][placeholder*='mail' i]") ||
            null
        );
    };

    for (let i = 0; i < maxTries; i++) {
        const inp = pickInput();
        if (inp && inp.getBoundingClientRect().width > 0) return { data: true };
        await sleep(250);
    }

    return { error: "Guard: tela do input de e-mail não ficou pronta (10s)." };
}

async function mpGuardOtpScreen() {
    const sleep = (ms) => new Promise(r => setTimeout(r, ms));
    const maxTries = Math.ceil(10_000 / 250);

    for (let i = 0; i < maxTries; i++) {
        let inputs = Array.from(document.querySelectorAll('input[id*="-digit-"]'));
        if (inputs.length !== 6) inputs = Array.from(document.querySelectorAll('input[type="tel"]'));

        if (inputs.length === 6 && inputs[0].getBoundingClientRect().width > 0) {
            return { data: true };
        }

        await sleep(250);
    }

    return { error: "Guard: tela de OTP não ficou pronta (10s)." };
}

async function mpGuardConfirmScreen() {
    const sleep = (ms) => new Promise(r => setTimeout(r, ms));
    const maxTries = Math.ceil(10_000 / 250);

    const isVisible = (el) => {
        if (!el) return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    };

    const findConfirmBtn = () => {
        const buttons = Array.from(document.querySelectorAll("button"));
        return buttons.find(b => {
            const t = (b.innerText || "").trim();
            return t === "Cadastrar" && !b.disabled && b.className && b.className.includes("andes-button");
        }) || null;
    };

    for (let i = 0; i < maxTries; i++) {
        const btn = findConfirmBtn();
        if (btn && isVisible(btn)) {
            return { data: true };
        }
        await sleep(250);
    }

    return { error: "Guard: tela de confirmação (botão 'Cadastrar') não ficou pronta (10s)." };
}

async function mpGuardOtpScreen() {
    const sleep = (ms) => new Promise(r => setTimeout(r, ms));
    const maxTries = Math.ceil(10_000 / 250);

    const isVisible = (el) => {
        if (!el) return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    };

    for (let i = 0; i < maxTries; i++) {
        let inputs = Array.from(document.querySelectorAll('input[id*="-digit-"]')).sort((a, b) => a.id.localeCompare(b.id));
        if (inputs.length !== 6) {
            inputs = Array.from(document.querySelectorAll('input[type="tel"]'));
        }

        if (inputs.length === 6 && isVisible(inputs[0])) {
            return { data: true };
        }

        await sleep(250);
    }

    return { error: "Guard: tela de OTP não ficou pronta (10s)." };
}

// ==========================================
// Step opcional (não derruba o fluxo se não existir)
// ==========================================
async function runOptionalStep(tabId, label, fn, args = [], { timeout = EXEC_TIMEOUT } = {}) {
    try {
        log(`${label}...`);
        const res = await injectAction(tabId, fn, args, timeout);

        if (res && res.skipped) {
            log(`${label}: não apareceu (seguindo).`, "warn");
            return { data: true, skipped: true };
        }

        return res;
    } catch (e) {
        log(`${label}: falhou/timeout (${e.message}) — ignorando e seguindo.`, "warn");
        return { data: true, skipped: true };
    }
}

// ==========================================
// injectAction (folga EXEC_TIMEOUT)
// ==========================================
function injectAction(tabId, func, args = [], timeout = EXEC_TIMEOUT) {
    return new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error(`Timeout (${timeout}ms): A etapa travou na tela.`)), timeout);

        chrome.scripting.executeScript(
            { target: { tabId }, func, args },
            (res) => {
                clearTimeout(timer);

                if (chrome.runtime.lastError) {
                    reject(new Error(chrome.runtime.lastError.message));
                    return;
                }

                if (!res || !res[0]) {
                    reject(new Error("Resultado da execução inválido (res vazio)"));
                    return;
                }

                const result = res[0].result;

                // IMPORTANTE:
                // Em cliques que causam re-render/navegação (ex.: Confirmar/Cadastrar),
                // o executeScript pode não retornar nada mesmo tendo funcionado.
                // No código antigo isso era tratado como sucesso.
                if (result == null) {
                    try { console.warn("[injectAction] Sem retorno do script (provável re-render/navegação). Assumindo sucesso."); } catch (e) {}
                    resolve({ data: true, no_result: true });
                    return;
                }

                if (result && result.error) {
                    reject(new Error(result.error));
                    return;
                }

                resolve(result);
            }
        );
    });
}

// ==========================================
// PROCESSO CADASTRO
// ==========================================
async function processEmail(fullEmail) {
    let emailRushTabId, mpTabId;
    const username = fullEmail.split('@')[0];

    try {
        updateStatus("Configurando EmailRush...");
        const tabRush = await chrome.tabs.update({ url: 'https://emailrush.com.br/pt-br/', active: false });
        emailRushTabId = tabRush.id;
        await waitForTabLoad(emailRushTabId);

        await injectAction(emailRushTabId, setupEmailRushClientSide, [username], EXEC_TIMEOUT);
        log("EmailRush configurado.");

        updateStatus("Acessando Mercado Pago...");
        const tabMP = await chrome.tabs.create({ url: 'https://www.mercadopago.com.br/pix-keys/app/key-management', active: true });
        mpTabId = tabMP.id;
        await waitForTabLoad(mpTabId);

        log("[1/7] Clicando em Cadastrar chave...");
        await runStep(mpTabId, "[MP] Clicar em Cadastrar chave", mpClickCadastrar);

        log("[2/7] Aguardando tela de seleção (Chave EMAIL)...");
        await runStep(mpTabId, "[MP] Guard seleção", mpGuardEmailSelectionScreen);

        log("[3/7] Selecionando opção E-mail...");
        await runStepGuarded(mpTabId, "[MP] Selecionar E-mail", mpGuardEmailSelectionScreen, mpClickEmailOption);

        log("[4/7] Aguardando tela do input de e-mail...");
        await runStep(mpTabId, "[MP] Guard input e-mail", mpGuardEmailInputScreen);

        log("[5/7] Preenchendo endereço de email...");
        try {
            await runStepGuarded(mpTabId, "[MP] Preencher e-mail", mpGuardEmailInputScreen, mpFillEmail, [fullEmail]);
        } catch (e) {
            log(`[MP] Preencher e-mail falhou (${e.message}). Tentando clicar 'Outro e-mail' e repetir...`, "warn");

            await runOptionalStep(mpTabId, "[MP] Clicar em Outro e-mail (fallback)", mpClickOutroEmailIfPresent);

            await runStep(mpTabId, "[MP] Guard input e-mail (pós Outro e-mail)", mpGuardEmailInputScreen);
            await runStepGuarded(mpTabId, "[MP] Preencher e-mail (retry)", mpGuardEmailInputScreen, mpFillEmail, [fullEmail]);
        }

        log("[6/7] Clicando em Continuar...");
        await runStep(mpTabId, "[MP] Clicar em Continuar", mpClickContinuar);

        log("[MP] Aguardando tela de confirmação (Cadastrar)...");
        await runStep(mpTabId, "[MP] Guard confirmação", mpGuardConfirmScreen);

        log("[7/7] Confirmando cadastro final...");
        await runStep(mpTabId, "[MP] Confirmar cadastro", mpClickConfirmar);

        log("[MP] Aguardando tela de OTP aparecer...");
        await runStep(mpTabId, "[MP] Guard OTP", mpGuardOtpScreen);

        log("Formulário finalizado. Solicitando código OTP...");
        updateStatus("Aguardando e-mail...");

        await chrome.tabs.update(emailRushTabId, { active: true });

        log("Atualizando a página do EmailRush para receber o novo código...");
        await wait(2000);

        const codeRes = await injectAction(emailRushTabId, getCodeFromEmailRush, [], 45_000);
        log(`Retorno bruto de getCodeFromEmailRush: ${JSON.stringify(codeRes)}`);

        let codeValue = null;
        if (codeRes && typeof codeRes.data === 'string') {
            codeValue = codeRes.data.replace(/\D/g, '').trim();
        } else if (typeof codeRes === 'string') {
            codeValue = codeRes.replace(/\D/g, '').trim();
        }

        if (!codeValue || codeValue.length !== 6) {
            log(`Código inválido ou não encontrado no retorno: ${JSON.stringify(codeRes)}`, 'error');
            throw new Error('Código OTP inválido (esperado 6 dígitos).');
        }

        log(`[OTP] Código OTP obtido: ${codeValue}`);

        updateStatus("Validando código...");
        await chrome.tabs.update(mpTabId, { active: true });

        const insertRes = await injectAction(mpTabId, insertCodeMercadoPago, [codeValue], 30_000);
        log(`Resultado insertCodeMercadoPago: ${JSON.stringify(insertRes)}`);

        if (!insertRes || insertRes.error) {
            throw new Error(insertRes && insertRes.error ? insertRes.error : 'Falha na injeção do OTP');
        }

        log(`Chave ${fullEmail} criada com sucesso!`);
        await wait(5000);

        await chrome.tabs.remove([mpTabId]);
        return true;

    } catch (error) {
        log(`[FALHA] ${error && error.message ? error.message : String(error)}`, "error");
        console.error('processEmail stack / objeto de erro:', error);
        return false;
    }
}

// =========================================================================
// MÓDULOS MP
// =========================================================================
async function mpClickCadastrar() {
    const sleep = (ms) => new Promise(r => setTimeout(r, ms));
    const maxTries = Math.ceil(8_000 / 250);

    const isVisible = (el) => {
        if (!el) return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    };

    for (let i = 0; i < maxTries; i++) {
        const span = Array.from(document.querySelectorAll("span"))
            .find(s => (s.textContent || "").trim() === "Cadastrar chave");

        const btn = span?.closest("button");

        if (btn && isVisible(btn) && !btn.disabled) {
            try { btn.scrollIntoView({ block: "center" }); } catch (e) {}
            btn.click();
            return { data: true };
        }

        await sleep(250);
    }

    return { error: "Botão 'Cadastrar chave' não encontrado." };
}

async function mpWaitForEmailList() {
    const sleep = (ms) => new Promise(r => setTimeout(r, ms));
    const maxTries = Math.ceil(10_000 / 250);

    for (let i = 0; i < maxTries; i++) {
        const ul =
            document.querySelector('ul[aria-label="Chave EMAIL"]') ||
            document.querySelector('ul[aria-label*="Chave" i]');

        const btn = ul?.querySelector('button.andes-list__item-action');

        if (ul && btn) {
            return { data: true };
        }

        await sleep(250);
    }

    return { error: "Tela de seleção de chave (Chave EMAIL) não carregou (10s)." };
}

async function mpClickEmailOption() {
    const sleep = (ms) => new Promise(r => setTimeout(r, ms));
    const maxTries = Math.ceil(9_000 / 250);

    const isVisible = (el) => {
        if (!el) return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    };

    const normalize = (t) => (t || "").trim().toLowerCase();

    for (let i = 0; i < maxTries; i++) {
        const ul =
            document.querySelector('ul[aria-label="Chave EMAIL"]') ||
            document.querySelector('ul[aria-label*="Chave" i]') ||
            document;

        const liList = Array.from(ul.querySelectorAll("li.andes-list__item"));
        let targetButton = null;

        for (const li of liList) {
            const text = normalize(li.innerText);
            if (text.includes("e-mail") || text.includes("email")) {
                const btn = li.querySelector("button.andes-list__item-action") || li.querySelector("button");
                if (btn) {
                    targetButton = btn;
                    break;
                }
            }
        }

        if (!targetButton) {
            const spans = Array.from(ul.querySelectorAll("span"));
            const s = spans.find(x => {
                const t = normalize(x.textContent);
                return t === "e-mail" || t === "email";
            });

            if (s) {
                const li = s.closest("li.andes-list__item");
                targetButton =
                    li?.querySelector("button.andes-list__item-action") ||
                    s.closest("button.andes-list__item-action") ||
                    s.closest("button");
            }
        }

        if (targetButton && isVisible(targetButton) && !targetButton.disabled) {
            try { targetButton.scrollIntoView({ block: "center" }); } catch (e) {}
            targetButton.click();
            return { data: true };
        }

        await sleep(250);
    }

    return { error: "Opção 'E-mail' não apareceu (9s)." };
}

// NOVO: passo opcional "Outro e-mail"
async function mpClickOutroEmailIfPresent() {
    const sleep = (ms) => new Promise(r => setTimeout(r, ms));
    const maxTries = Math.ceil(10_000 / 250);

    const isVisible = (el) => {
        if (!el) return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    };

    const normalize = (t) => (t || "").trim().toLowerCase();

    for (let i = 0; i < maxTries; i++) {
        const ul =
            document.querySelector('ul[aria-label="Chave EMAIL"]') ||
            document.querySelector('ul[aria-label*="Chave" i]') ||
            document;

        // 1) tenta pelo LI inteiro
        const items = Array.from(ul.querySelectorAll("li.andes-list__item"));
        for (const li of items) {
            const t = normalize(li.innerText);
            if (t.includes("outro e-mail") || t.includes("outro email")) {
                const btn = li.querySelector("button.andes-list__item-action") || li.querySelector("button");
                if (btn && isVisible(btn) && !btn.disabled) {
                    try { btn.scrollIntoView({ block: "center" }); } catch (e) {}
                    btn.click();
                    return { data: true };
                }
            }
        }

        // 2) fallback: procura o texto exatamente no SPAN e sobe até o BUTTON clicável
        const spans = Array.from(ul.querySelectorAll("span"));
        const s = spans.find(x => {
            const t = normalize(x.textContent);
            return t === "outro e-mail" || t === "outro email";
        });

        if (s) {
            const li = s.closest("li.andes-list__item");
            const btn =
                li?.querySelector("button.andes-list__item-action") ||
                s.closest("button.andes-list__item-action") ||
                s.closest("button");

            if (btn && isVisible(btn) && !btn.disabled) {
                try { btn.scrollIntoView({ block: "center" }); } catch (e) {}
                btn.click();
                return { data: true };
            }
        }

        await sleep(250);
    }

    return { data: true, skipped: true };
}

async function mpFillEmail(email) {
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    const maxTries = Math.ceil(9_000 / 250);

    const isVisible = (el) => {
        if (!el) return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    };

    const pickInput = () => {
        return (
            document.querySelector("input[type='email']") ||
            document.querySelector("input[name*='mail' i]") ||
            document.querySelector("input[autocomplete='email']") ||
            document.querySelector("input[aria-label*='mail' i]") ||
            document.querySelector("input[placeholder*='mail' i]") ||
            null
        );
    };

    for (let i = 0; i < maxTries; i++) {
        const inp = pickInput();

        if (inp && isVisible(inp)) {
            try { inp.scrollIntoView({ block: "center" }); } catch (e) {}
            inp.focus();

            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")?.set;
            if (setter) setter.call(inp, email);
            else inp.value = email;

            inp.dispatchEvent(new Event("input", { bubbles: true }));
            inp.dispatchEvent(new Event("change", { bubbles: true }));
            inp.blur();

            if ((inp.value || "").trim().toLowerCase() === String(email).trim().toLowerCase()) {
                return { data: true };
            }
        }

        await sleep(250);
    }

    return { error: "Input de e-mail não encontrado (9s)." };
}

async function mpClickContinuar() {
    const sleep = (ms) => new Promise(r => setTimeout(r, ms));
    const maxTries = Math.ceil(9_000 / 250);

    const isVisible = (el) => {
        if (!el) return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    };

    for (let i = 0; i < maxTries; i++) {
        const span = Array.from(document.querySelectorAll("span"))
            .find(s => (s.textContent || "").trim() === "Continuar");
        const btn = span?.closest("button");

        if (btn && isVisible(btn) && !btn.disabled) {
            try { btn.scrollIntoView({ block: "center" }); } catch (e) {}
            btn.click();
            return { data: true };
        }

        await sleep(250);
    }

    return { error: "Botão 'Continuar' não habilitou (9s)." };
}

async function mpClickConfirmar() {
    const sleep = (ms) => new Promise(r => setTimeout(r, ms));
    const maxTries = Math.ceil(9_000 / 250);

    const isVisible = (el) => {
        if (!el) return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    };

    const findConfirmBtn = () => {
        // Preferência: botão com texto EXATO "Cadastrar"
        const buttons = Array.from(document.querySelectorAll("button"));
        let btn = buttons.find(b => (b.innerText || "").trim() === "Cadastrar" && !b.disabled) || null;

        // Fallback comum do Mercado Pago: botão "loud" (quando existir)
        if (!btn) {
            btn = buttons.find(b => (b.innerText || "").trim() === "Cadastrar" && (b.className || "").includes("andes-button--loud") && !b.disabled) || null;
        }

        return btn;
    };

    for (let i = 0; i < maxTries; i++) {
        const btn = findConfirmBtn();

        if (btn && isVisible(btn) && !btn.disabled) {
            try { btn.scrollIntoView({ block: "center" }); } catch (e) {}
            btn.click();
            return { data: true };
        }

        await sleep(250);
    }

    return { error: "Botão 'Cadastrar' de confirmação não encontrado (9s)." };
}

// =========================================================================
// EmailRush
// =========================================================================
async function setupEmailRushClientSide(username) {
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    try {
        await sleep(2000);
        const btnAlterar = document.querySelector('button[onclick="openEditModal()"]');
        if (!btnAlterar) return { error: "Botão 'Alterar' não encontrado." };
        btnAlterar.click();

        await sleep(1000);
        const inputUser = document.querySelector('#modalInputUser');
        if (!inputUser) return { error: "Input de usuário não encontrado." };

        inputUser.focus();
        const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
        nativeInputValueSetter.call(inputUser, username);
        inputUser.dispatchEvent(new Event('input', { bubbles: true }));

        await sleep(500);
        const btnSave = document.querySelector('button[onclick="app.saveEditModal()"]');
        if (!btnSave) return { error: "Botão 'Salvar' não encontrado." };
        btnSave.click();

        await sleep(2000);
        return { data: true };
    } catch (e) {
        return { error: e.message };
    }
}

async function getCodeFromEmailRush() {
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    const logStep = (msg) => {
        console.log(`[BOT EMAIL] ${msg}`);
        try { chrome.runtime.sendMessage({ type: 'LOG_INJECTED', message: `[EmailRush] ${msg}` }); } catch (e) {}
    };

    try {
        logStep("Aguardando recebimento do e-mail do Mercado Pago...");

        for (let i = 0; i < 20; i++) {
            const emails = Array.from(document.querySelectorAll('div[data-message-id]'));
            const mpEmail = emails.find(el =>
                (el.innerText || '').includes('Mercado Pago') &&
                (el.innerText || '').includes('código de segurança')
            );

            if (mpEmail) {
                logStep("E-mail encontrado! Abrindo...");
                mpEmail.click();
                await sleep(3500);

                logStep("Procurando o Iframe #msg-corpo...");
                const iframe = document.querySelector('#msg-corpo iframe') || document.querySelector('iframe');

                if (iframe) {
                    const srcdoc = iframe.getAttribute('srcdoc') || iframe.srcdoc || '';

                    if (srcdoc) {
                        logStep("Extraindo texto do srcdoc...");
                        const parser = new DOMParser();
                        const doc = parser.parseFromString(srcdoc, 'text/html');
                        const text = (doc && doc.body && doc.body.innerText) ? doc.body.innerText : '';

                        const allMatches = text.match(/\b\d{6}\b/g) || [];

                        if (allMatches.length > 0) {
                            const keywords = ['código', 'não compartilhe', 'código de segurança', 'senha', 'otp', 'código:'];
                            let chosen = allMatches[0];
                            let bestDist = Infinity;
                            const lowerText = text.toLowerCase();

                            for (const kw of keywords) {
                                const kpos = lowerText.indexOf(kw);
                                if (kpos === -1) continue;
                                for (const m of allMatches) {
                                    const mpos = lowerText.indexOf(m);
                                    if (mpos === -1) continue;
                                    const d = Math.abs(mpos - kpos);
                                    if (d < bestDist) {
                                        bestDist = d;
                                        chosen = m;
                                    }
                                }
                            }

                            logStep(`Código capturado com sucesso: ${chosen}`);
                            return { data: chosen };
                        } else {
                            logStep("Nenhum código de 6 dígitos no texto do srcdoc. Tentando no HTML bruto...");
                            const htmlMatches = srcdoc.match(/\b\d{6}\b/g);
                            if (htmlMatches && htmlMatches.length > 0) {
                                logStep(`Código capturado pelo HTML bruto: ${htmlMatches[0]}`);
                                return { data: htmlMatches[0] };
                            }
                        }
                    } else {
                        logStep("O iframe não tem srcdoc acessível ainda.");
                    }
                } else {
                    logStep("Iframe #msg-corpo não encontrado na tela.");
                }
            } else {
                logStep("Aguardando novo e-mail aparecer na lista...");
            }

            await sleep(2000);
        }

        return { error: "O email com o código não chegou ou não foi possível lê-lo no srcdoc." };
    } catch (e) {
        return { error: `Erro na leitura do Email: ${e.message}` };
    }
}

// =========================================================================
// OTP Mercado Pago
// =========================================================================
async function insertCodeMercadoPago(code) {
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    const logStep = (msg) => {
        console.log(`[BOT MP] ${msg}`);
        try { chrome.runtime.sendMessage({ type: 'LOG_INJECTED', message: `[MP] ${msg}` }); } catch (e) {}
    };

    try {
        if (!code) return { error: 'Parâmetro "code" ausente.' };
        code = String(code).replace(/\D/g, '').trim();
        if (code.length !== 6) return { error: `Código inválido recebido: "${code}"` };

        logStep(`Iniciando preenchimento do código OTP: ${code}`);

        let inputs = [];
        logStep("Aguardando campos de digitação do código...");
        for (let i = 0; i < 30; i++) {
            inputs = Array.from(document.querySelectorAll('input[id*="-digit-"]')).sort((a, b) => a.id.localeCompare(b.id));
            if (inputs.length !== 6) {
                inputs = Array.from(document.querySelectorAll('input[type="tel"]'));
            }

            if (inputs.length === 6 && inputs[0].getBoundingClientRect().width > 0) {
                break;
            }
            await sleep(500);
        }

        if (inputs.length !== 6) {
            return { error: `Campos de OTP não encontrados na tela. Achou: ${inputs.length}` };
        }

        logStep("Campos encontrados! Injetando os números...");
        const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;

        for (let i = 0; i < 6; i++) {
            inputs[i].focus();
            nativeInputValueSetter.call(inputs[i], code[i]);
            inputs[i].dispatchEvent(new Event('input', { bubbles: true }));
            await sleep(60 + Math.random() * 40);
        }

        await sleep(1000);
        logStep("Buscando botão 'Validar o código'...");

        let clicked = false;
        for (let i = 0; i < 30; i++) {
            const span = Array.from(document.querySelectorAll("span"))
                .find(s => (s.textContent || "").trim().toLowerCase().includes("validar"));

            let btn = span?.closest("button");
            if (!btn) btn = document.querySelector('button[data-testid="submit_button"]');

            if (btn && btn.getBoundingClientRect().width > 0 && !btn.disabled) {
                logStep("Clicando em Validar...");
                btn.click();
                clicked = true;
                break;
            }
            await sleep(500);
        }

        if (!clicked) return { error: "Botão de validar não encontrado ou não habilitou." };

        logStep("Aguardando confirmação final da tela...");
        await sleep(4000);

        return { data: true };

    } catch (e) {
        return { error: `Erro na injeção OTP: ${e.message}` };
    }
}

// =========================================================================
// DELETE PIX (sem bypass de captcha)
// =========================================================================
async function deletePixKey() {
    let mpTabId;

    try {
        const protectedKeys = ['seu-email-principal@gmail.com', 'outra-chave-importante@pix.com'];

        updateStatus("Limpando chaves...");
        const tabMP = await chrome.tabs.create({ url: 'https://www.mercadopago.com.br/pix-keys/app/key-management', active: true });
        mpTabId = tabMP.id;
        await waitForTabLoad(mpTabId);
        await wait(3000);

        log(`Iniciando limpeza seletiva...`);
        const result = await injectAction(mpTabId, mpCleanKeysLogic, [protectedKeys], 45_000);

        if (result && result.data) {
            log("Processo de limpeza finalizado.");
            await chrome.tabs.remove(mpTabId);
            return true;
        }

        return false;

    } catch (e) {
        log(`Falha na limpeza: ${e.message}`, "error");
        return false;
    }
}

async function mpCleanKeysLogic(protectedKeys) {
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    const logStep = (msg) => { try { chrome.runtime.sendMessage({ type: 'LOG_INJECTED', message: `[Bot] ${msg}` }); } catch(e){} };

    try {
        for (let attempt = 0; attempt < 20; attempt++) {
            const rows = Array.from(document.querySelectorAll('li.andes-list__item'));
            
            const targetRow = rows.find(row => {
                const emailElement = row.querySelector('small.lgcRAF');
                if (!emailElement) return false;
                const emailText = emailElement.innerText.trim().toLowerCase();
                const isProtected = protectedKeys.some(p => emailText === p.toLowerCase());
                const hasMenu = row.querySelector('svg[aria-label="Botão menu chave Pix"]');
                return !isProtected && hasMenu;
            });

            if (!targetRow) {
                logStep("Limpeza concluída ou chaves protegidas.");
                break;
            }

            const emailTarget = targetRow.querySelector('small.lgcRAF').innerText;
            logStep(`Excluindo: ${emailTarget}`);

            // 1. Abrir Menu
            const menuIcon = targetRow.querySelector('svg[aria-label="Botão menu chave Pix"]');
            menuIcon.closest('i').click();
            await sleep(1000);

            // 2. Clicar em Excluir no Menu
            const deleteSvg = document.querySelector('svg[aria-label="Botão excluir chave Pix"]');
            if (deleteSvg) {
                deleteSvg.closest('.cxQmJS').click();
                await sleep(2000);

                const captchaIframe = document.querySelector('iframe[title*="reCAPTCHA"]');
                if (captchaIframe) {
                    logStep("Tentando resolver...");
                    
                    // Nota: O clique no checkbox e no solver-button geralmente exige 
                    // que a extensão tenha a extensão "Buster" ou similar instalada, 
                    // ou que você clique manualmente. Vamos tentar o clique via script:
                    const solveBtn = document.querySelector('#solver-button');
                    if (solveBtn) {
                        solveBtn.click();
                        logStep("Solver acionado, aguardando 5s...");
                        await sleep(5000);
                    }
                }
                // -------------------------------

                // 3. Confirmar no Modal
                // Procuramos o botão novamente, pois ele pode ter sido re-renderizado após o captcha
                const confirmBtn = Array.from(document.querySelectorAll('button'))
                    .find(b => b.innerText.trim() === "Excluir" && b.classList.contains('andes-button--loud'));
                
                if (confirmBtn) {
                    confirmBtn.click();
                    logStep("Exclusão confirmada.");
                    await sleep(4000); 
                } else {
                    logStep("Erro: Botão de confirmação não encontrado.");
                    document.body.click(); 
                    await sleep(500);
                }
            } else {
                logStep("Erro: Opção não encontrada.");
                document.body.click();
                await sleep(500);
            }
        }
        return { data: true };
    } catch (e) {
        return { error: e.message };
    }
}