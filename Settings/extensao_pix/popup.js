const logArea = document.getElementById('logArea');
const statusIndicator = document.getElementById('statusIndicator');

// Função que puxa os logs do banco de dados interno da extensão
function refreshUI() {
    chrome.storage.local.get(['automationLogs', 'automationStatus'], (data) => {
        if (data.automationLogs) {
            logArea.innerHTML = data.automationLogs;
            logArea.scrollTop = logArea.scrollHeight;
        } else {
            logArea.innerHTML = '';
        }
        if (data.automationStatus) statusIndicator.innerText = data.automationStatus;
    });
}

// Carrega assim que abre o popup
refreshUI();

// Se o background avisar que tem log novo, atualiza a tela
chrome.runtime.onMessage.addListener((msg) => {
    if (msg.type === 'LOG_UPDATED' || msg.type === 'STATUS_UPDATED') {
        refreshUI();
    }
});

// Botões
// Adicione isso ao final do seu popup.js
document.getElementById('btnDelete').addEventListener('click', () => {
    chrome.runtime.sendMessage({ action: 'START_DELETION' });
});

document.getElementById('btnStart').addEventListener('click', () => chrome.runtime.sendMessage({ action: 'START_REGISTRATION' }));
document.getElementById('btnStop').addEventListener('click', () => chrome.runtime.sendMessage({ action: 'STOP_AUTOMATION' }));
document.getElementById('btnClearLogs').addEventListener('click', () => {
    chrome.storage.local.remove('automationLogs', refreshUI);
});