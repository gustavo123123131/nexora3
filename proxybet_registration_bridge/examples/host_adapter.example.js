'use strict';
// ADAPTE ao controlador de navegadores que seu projeto JÁ tem.
// Este exemplo não cria navegadores, não acessa sites e não faz cadastro.
function makeHostAdapter(existingBrowserService){
  if(!existingBrowserService)throw new Error('Injete o controlador de navegadores existente');
  return {
    openProfile: ({profileId,url})=>existingBrowserService.openExistingProfile(profileId,url),
    navigate: ({handle,url})=>existingBrowserService.navigate(handle,url),
    inspectFields: ({handle,selectors})=>existingBrowserService.inspectFields(handle,selectors),
    fillFields: ({handle,selectors,values})=>existingBrowserService.fillForm(handle,selectors,values),
    submitForm: ({handle,selector})=>existingBrowserService.clickSubmit(handle,selector),
    waitForRegistrationOutcome: ({handle,siteId})=>existingBrowserService.awaitVerifiedRegistration(handle,siteId),
  };
}
module.exports={makeHostAdapter};
