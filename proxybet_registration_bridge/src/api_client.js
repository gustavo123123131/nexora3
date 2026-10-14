'use strict';
const {sameOriginUrl}=require('./site_config');
class AuthorizedApiClient {
  constructor({site,fetchImpl=globalThis.fetch,tokenProvider=async()=>null}){
    if(!site?.officialApi)throw new Error('Nenhuma API oficial autorizada foi configurada');
    if(typeof fetchImpl!=='function')throw new Error('Implementação HTTP ausente');
    this.config=site.officialApi;this.fetch=fetchImpl;this.tokenProvider=tokenProvider;
  }
  async call(operationName,{body,signal}={}){
    const op=this.config.operations.find(item=>item.name===operationName);
    if(!op)throw new Error('Operação não autorizada: '+operationName);
    if(body!==undefined && !['POST','PUT','PATCH'].includes(op.method))throw new Error('Esse método não permite body');
    const url=sameOriginUrl(new URL(this.config.baseUrl),op.path);
    const token=await this.tokenProvider();
    const headers={'Accept':'application/json'};
    if(body!==undefined)headers['Content-Type']='application/json';
    if(token)headers['Authorization']='Bearer '+token;
    const response=await this.fetch(url,{method:op.method,headers,body:body===undefined?undefined:JSON.stringify(body),signal,redirect:'error',credentials:'omit'});
    // A camada HTTP não infere êxito de operação de negócio só por HTTP 200.
    if(!response.ok)throw new Error('Falha HTTP '+response.status);
    const ct=response.headers?.get?.('content-type')||'';
    if(!ct.includes('application/json'))throw new Error('Resposta inesperada');
    return response.json();
  }
}
module.exports={AuthorizedApiClient};
