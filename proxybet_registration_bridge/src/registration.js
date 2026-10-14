'use strict';
const {validateFields}=require('./validation');
const {assertBrowserAdapter}=require('./browser_adapter');
class RegistrationService {
  constructor({sites,store,browser,log=()=>{}}){
    if(!sites||!store)throw new Error('sites e store obrigatórios');
    this.sites=sites;this.store=store;this.browser=assertBrowserAdapter(browser);this.log=log;
    this.sessions=new Map();
  }
  async prepare({siteId,profileId,loginIdentifier=''}) {
    const site=this.sites.get(siteId);
    if(typeof profileId!=='string'||!profileId.trim()||profileId.length>128)throw new Error('Perfil inválido');
    const handle=await this.browser.openProfile({profileId,url:site.registrationUrl});
    await this.browser.navigate({handle,url:site.registrationUrl});
    const selectors=Object.fromEntries(Object.entries(site.fields).map(([key,v])=>[key,v.selector]));
    const found=await this.browser.inspectFields({handle,selectors});
    const missingRequired=Object.keys(site.fields).filter(k=>site.fields[k].required&&!found?.[k]);
    const record=this.store.create({siteId,profileId,loginIdentifier});
    this.sessions.set(record.id,{handle,site,found,profileId});
    this.log('registration.prepared',{accountId:record.id,siteId,stage:'review',fieldCount:Object.keys(found||{}).length});
    return {account:record,registrationUrl:site.registrationUrl,fields:Object.entries(site.fields).map(([name,def])=>({name,required:def.required,present:!!found?.[name]})),missingRequired};
  }
  async submit({accountId,values,userConfirmed=false}) {
    if(userConfirmed!==true)throw new Error('Confirmação explícita do usuário necessária');
    const session=this.sessions.get(accountId);
    if(!session)throw new Error('Preparação expirada: abra o formulário novamente');
    const {site,handle,found}=session;
    const absent=Object.keys(site.fields).filter(k=>site.fields[k].required&&!found?.[k]);
    if(absent.length)return {account:this.store.update(accountId,{status:'failed',stage:'missing-fields'}),errors:{fields:'Campos obrigatórios não encontrados: '+absent.join(', ')}};
    const validated=validateFields(values,site.fields);
    if(!validated.ok)return {account:this.store.get(accountId),errors:validated.errors};
    const selectors=Object.fromEntries(Object.entries(site.fields).filter(([key])=>found?.[key]).map(([key,val])=>[key,val.selector]));
    const allowedValues=Object.fromEntries(Object.entries(validated.values).filter(([key])=>key in selectors));
    try {
      await this.browser.fillFields({handle,selectors,values:allowedValues});
      this.store.update(accountId,{status:'pending',stage:'submitted'});
      await this.browser.submitForm({handle,selector:site.submitSelector});
      const outcome=await this.browser.waitForRegistrationOutcome({handle,siteId:site.id});
      return this._applyOutcome(accountId,outcome);
    } catch(err) {
      this.log('registration.error',{accountId,siteId:site.id,stage:'submit-error'});
      return {account:this.store.update(accountId,{status:'failed',stage:'submit-error'}),errors:{operation:'Não foi possível confirmar o envio; consulte a plataforma'}};
    } finally { // Credenciais transitórias não são guardadas pelo módulo.
      for(const key of Object.keys(allowedValues))delete allowedValues[key];
    }
  }
  async refresh({accountId}){
    const session=this.sessions.get(accountId);
    if(!session)throw new Error('Sessão não disponível; confira o cadastro diretamente no site');
    return this._applyOutcome(accountId,await this.browser.waitForRegistrationOutcome({handle:session.handle,siteId:session.site.id}));
  }
  _applyOutcome(accountId,outcome){
    const allowed=['completed','pending','failed'];
    const status=allowed.includes(outcome?.status)?outcome.status:'pending';
    // Nunca declarar concluído com base somente em HTTP 200 ou clique enviado:
    // o adaptador deve atestar sinal positivo do provedor.
    const stage=typeof outcome?.stage==='string' && /^[a-z0-9_-]{1,60}$/i.test(outcome.stage) ? outcome.stage : status==='pending'?'verification-required':status;
    const account=this.store.update(accountId,{status,stage});
    this.log('registration.outcome',{accountId,siteId:account.siteId,status,stage});
    if(status!=='pending')this.sessions.delete(accountId);
    return {account};
  }
}
module.exports={RegistrationService};
