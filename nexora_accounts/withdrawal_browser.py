"""Read-only withdrawal preflight through the selected Chromium page's SDK.

The ProxyBet research contains synthetic cashV3/orderInfo examples only.
No request to cashV3 is permitted until the real production contract,
withdrawable balance and destination semantics are confirmed.
"""
from urllib.parse import urlsplit

ALLOWED_HOSTS = frozenset(("5gbet.com", "www.5gbet.com"))

_JS = r"""async () => {
  const trace=[];
  let stage='client';
  const mark=(event,status=null,code=null,elapsedMs=null)=>{
    if(trace.length<25)trace.push({stage,event,
      httpStatus:Number.isSafeInteger(status)?status:null,
      providerCode:Number.isSafeInteger(code)?code:null,
      elapsedMs:Number.isSafeInteger(elapsedMs)?elapsedMs:null});
  };
  const stop=(message)=>{mark('result');return {stage,message,trace};};
  if(location.protocol!=='https:' ||
     !['www.5gbet.com','5gbet.com'].includes(location.hostname.toLowerCase())){
    stage='site';return stop('Navegador fora do domínio autorizado.');
  }
  const urls=[...performance.getEntriesByType('resource').map(x=>x.name),
    ...Array.from(document.querySelectorAll('script[src]'),x=>x.src)];
  const chunk=[...new Set(urls)].find(x=>{
    try {
      const u=new URL(x,location.href);
      return u.origin===location.origin &&
        /\/commonChunk\.[^/?]+\.js$/i.test(u.pathname);
    }catch(_){return false;}
  });
  if(!chunk)return stop('Não foi possível carregar o cliente autenticado da página.');
  let sdk;
  try{
    sdk=(await import(chunk)).n;
    if(typeof sdk?.request!=='function')throw Error('unavailable');
  }catch(_){return stop('Não foi possível carregar o cliente autenticado da página.');}
  class Failure extends Error {}
  async function post(path,data){
    const start=performance.now();
    mark('request_start');
    let response;
    try{
      response=await sdk.request(
        {url:'/api/'+path,method:'POST',...(data===undefined?{}:{data})},
        {merge:false,maxRetryCount:0,cache:{useCache:false}}
      );
    }catch(err){
      const status=Number(err?.response?.status);
      const code=Number(err?.response?.data?.code ?? err?.data?.code);
      mark('request_exception',Number.isSafeInteger(status)?status:null,
        Number.isSafeInteger(code)?code:null,Math.round(performance.now()-start));
      throw new Failure();
    }
    const body=response?.data ?? response;
    const code=Number(body?.code);
    const status=Number(response?.status);
    mark(code===1?'api_accepted':'api_rejected',
      Number.isSafeInteger(status)?status:null,
      Number.isSafeInteger(code)?code:null,Math.round(performance.now()-start));
    if(code!==1)throw new Failure();
    return body?.data ?? {};
  }
  try{
    stage='withdrawInfoV2';
    const info=await post('finance/certify/withdrawInfoV2');
    if(!info || typeof info!=='object')return stop('Dados de saque não retornaram no formato esperado.');
    mark('shape_checked');
    if(info.hasWithdrawPasswd!==true)
      return stop('PIN de saque não confirmado pela plataforma.');
    // The source research only confirms "accounts" for withdrawal destinations.
    if(!Array.isArray(info.accounts) || !info.accounts.length)
      return stop('Nenhum destino de saque vinculado nesta conta.');
    if(info.accounts.length!==1)
      return stop('Mais de um destino remoto: seleção precisa de contrato validado.');
    const target=info.accounts[0];
    if(!target || (typeof target.id!=='string' && typeof target.id!=='number')
      || !String(target.id).trim())
      return stop('Conta de destino sem identificador remoto comprovado.');
    const min=Number(info.withdrawMin), max=Number(info.withdrawMax);
    if(!Number.isFinite(min)||!Number.isFinite(max)||min<=0||max<min)
      return stop('Limites de saque não foram identificados.');
    stage='gameCenter/gold';
    const wallet=await post('gameCenter/gold');
    const rawBalance=wallet?.userInfos?.game_gold;
    if(rawBalance===null || rawBalance===undefined ||
       !Number.isFinite(Number(rawBalance)) || Number(rawBalance)<0)
      return stop('Saldo realmente sacável não foi identificado em userInfos.game_gold.');
    const amount=Math.min(Math.max(0,Math.floor(Number(rawBalance))),max);
    if(!Number.isFinite(amount)||amount<min)
      return stop('Saldo disponível inferior ao mínimo permitido.');
    const method=target?.withdrawTypeId;
    if(!Number.isSafeInteger(method)||method<0)
      return stop('Tipo de saque remoto não identificado.');
    stage='getWithdrawFee';
    const quote=await post('finance/certify/getWithdrawFee',{
      withdrawTypeId:method,amount,time:Math.floor(Date.now()/1000)
    });
    const fee=quote?.withdrawFee ?? quote?.fee;
    if(fee===null||fee===undefined||!Number.isFinite(Number(fee)) ||
       Number(fee)<0||Number(fee)>amount)
      return stop('Taxa do saque não foi confirmada.');
    stage='contract';
    return stop('Consultas de saque válidas; envio financeiro requer contrato autorizado.');
  }catch(_){
    if(stage==='withdrawInfoV2')return stop('Não foi possível consultar a conta de saque.');
    if(stage==='gameCenter/gold')return stop('Consulta de saldo falhou ou não é suportada.');
    if(stage==='getWithdrawFee')return stop('Consulta de taxa falhou ou não é suportada.');
    return stop('Pré-requisitos não verificados; nenhum saque enviado.');
  }
}"""


def inspect_withdrawal_preflight(page):
    try:
        origin = urlsplit(str(page.url or ""))
        if origin.scheme != "https" or origin.hostname not in ALLOWED_HOSTS:
            return {"stage": "site", "message": "Navegador fora do domínio autorizado.",
                    "trace": []}
        result = page.evaluate(_JS)
        if not isinstance(result, dict):
            return {"stage": "client",
                    "message": "Não foi possível carregar o cliente autenticado da página.",
                    "trace": []}
        # Never return raw SDK errors, destination, PIN, balances or tokens.
        return {
            "stage": result.get("stage"),
            "message": result.get("message"),
            "trace": result.get("trace", []),
        }
    except Exception:
        return {"stage": "client",
                "message": "Não foi possível carregar o cliente autenticado da página.",
                "trace": []}
