'use strict';
// Esboço de integração: execute APENAS no processo principal Electron existente.
// Antes de expor handlers, implemente checkAuthorizedSender para a URL real
// do renderer do seu aplicativo. Não exponha esses canais a páginas externas.
function registerAccountHandlers({ipcMain,registration,launcher,store,checkAuthorizedSender}) {
  if(typeof checkAuthorizedSender!=='function')throw new Error('Validação de remetente obrigatória');
  const wrap=(fn)=>async (event,payload)=>{
    if(!checkAuthorizedSender(event))throw new Error('Remetente não autorizado');
    return fn(payload||{});
  };
  ipcMain.handle('accounts:prepare',wrap(p=>registration.prepare(p)));
  ipcMain.handle('accounts:submit',wrap(p=>registration.submit(p)));
  ipcMain.handle('accounts:refresh',wrap(p=>registration.refresh(p)));
  ipcMain.handle('accounts:list',wrap(p=>store.list(p)));
  ipcMain.handle('accounts:open',wrap(p=>launcher.open(p.accountId)));
}
module.exports={registerAccountHandlers};
