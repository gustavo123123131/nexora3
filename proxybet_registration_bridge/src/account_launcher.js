'use strict';
const {assertBrowserAdapter}=require('./browser_adapter');
class AccountLauncher {
  constructor({sites,store,browser,credentialProvider=null,log=()=>{}}){
    this.sites=sites;this.store=store;this.browser=assertBrowserAdapter(browser);this.log=log;this.credentialProvider=credentialProvider;
  }
  async open(accountId){
    const account=this.store.get(accountId);
    if(!account)throw new Error('Conta não encontrada');
    const site=this.sites.get(account.siteId);
    // openProfile deve usar o perfil já existente sem copiar cookies ou exportar tokens.
    const handle=await this.browser.openProfile({profileId:account.profileId,url:site.loginUrl});
    await this.browser.navigate({handle,url:site.loginUrl});
    if(this.credentialProvider && Object.keys(site.loginFields).length) {
      // Cofre seguro externo fornece credenciais em memória e sob solicitação do usuário.
      const credentials=await this.credentialProvider({accountId:account.id,siteId:site.id});
      if(credentials) {
        const selectors=Object.fromEntries(Object.entries(site.loginFields).map(([key,val])=>[key,val.selector]));
        const present=await this.browser.inspectFields({handle,selectors});
        const usable=Object.fromEntries(Object.entries(selectors).filter(([k])=>present?.[k]));
        const values=Object.fromEntries(Object.entries(credentials).filter(([k])=>k in usable && ['username','email','password'].includes(k)));
        await this.browser.fillFields({handle,selectors:usable,values});
        // Não clica em login: etapa adicional de verificação permanece com usuário.
      }
    }
    this.log('account.opened',{accountId:account.id,siteId:site.id,profileId:account.profileId});
    return {account,loginUrl:site.loginUrl,requiresUserAuthentication:true};
  }
}
module.exports={AccountLauncher};
