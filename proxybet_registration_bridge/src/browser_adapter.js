'use strict';
/** Contrato a implementar pelo backend EXISTENTE de Electron/Chromium.
 * Não cria janelas nem perfis novos e não manipula cookies.
 * openProfile({profileId,url}) -> handle
 * navigate({handle,url}) -> void
 * inspectFields({handle,selectors}) -> { username: true, password: true, ... }
 * fillFields({handle,values,selectors}) -> void  (somente campos permitidos)
 * submitForm({handle,selector}) -> void
 * waitForRegistrationOutcome({handle,siteId}) -> {status:'completed'|'pending'|'failed',stage?: string}
 * As condições de sucesso precisam ser configuradas e confirmadas pelo site.
 */
function assertBrowserAdapter(adapter){
  const names=['openProfile','navigate','inspectFields','fillFields','submitForm','waitForRegistrationOutcome'];
  for(const key of names)if(typeof adapter?.[key]!=='function')throw new Error('Adapter de navegador precisa implementar '+key);
  return adapter;
}
module.exports={assertBrowserAdapter};
