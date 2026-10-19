"""Read-only withdrawal preflight through the selected Chromium page's SDK.

The ProxyBet research contains synthetic cashV3/orderInfo examples only.
No request to cashV3 is permitted until the real production contract,
withdrawable balance and destination semantics are confirmed.
"""
from urllib.parse import urlsplit

ALLOWED_HOSTS = frozenset(("5gbet.com", "www.5gbet.com"))


def _account_hosts(start_url):
    """Resolve only the exact account site and its optional www redirect.

    No arbitrary-site fallback: the origin is the one saved in this account.
    """
    try:
        target = urlsplit(str(start_url or ""))
        host = target.hostname
        if target.scheme != "https" or not host or target.username or target.password:
            return frozenset()
        if target.port not in (None, 443):
            return frozenset()
        if not all(ch in "abcdefghijklmnopqrstuvwxyz0123456789.-" for ch in host):
            return frozenset()
        bare = host[4:] if host.startswith("www.") else host
        if "." not in bare or bare.startswith(".") or bare.endswith("."):
            return frozenset()
        return frozenset((bare, "www." + bare))
    except (ValueError, AttributeError, TypeError):
        return frozenset()

_JS = r"""async ({allowedHosts}={}) => {
  const trace=[];
  let stage='client';
  const mark=(event,status=null,code=null,elapsedMs=null)=>{
    if(trace.length<25)trace.push({stage,event,
      httpStatus:Number.isSafeInteger(status)?status:null,
      providerCode:Number.isSafeInteger(code)?code:null,
      elapsedMs:Number.isSafeInteger(elapsedMs)?elapsedMs:null});
  };
  // Only enumerated PIN-field metadata may leave this page; no raw payloads.
  let pinDiagnostic=null, securityDiagnostic=null;
  const stop=(message)=>{mark('result');return {
    stage,message,trace,
    ...(pinDiagnostic===null?{}:{pinDiagnostic}),
    ...(securityDiagnostic===null?{}:{securityDiagnostic})
  };};
  const allowed=Array.isArray(allowedHosts)&&allowedHosts.length?
    allowedHosts:['www.5gbet.com','5gbet.com'];
  if(location.protocol!=='https:' ||
     !allowed.includes(location.hostname.toLowerCase())){
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
    const present=Object.prototype.hasOwnProperty.call(info,'hasWithdrawPasswd');
    const raw=info.hasWithdrawPasswd;
    pinDiagnostic={
      value:!present?'ausente':raw===true?'true':raw===false?'false':'outro',
      type:!present?'undefined':raw===null?'null':typeof raw
    };
    if(info.hasWithdrawPasswd!==true){
      // The withdrawal endpoint sometimes omits this flag. Inspect the
      // independently exposed security status without sending a PIN or payout.
      if(!present){
        try{
          const res=await sdk.request({url:'/api/member/user/security/status',
            method:'POST',timeout:18000},
            {merge:false,maxRetryCount:0,cache:{useCache:false}});
          if(Number(res?.data?.code)===1){
            const data=res?.data?.data;
            const known=!!data && typeof data==='object' &&
              Object.prototype.hasOwnProperty.call(data,'hasWithdrawPasswd');
            const v=known?data.hasWithdrawPasswd:undefined;
            securityDiagnostic={
              status:'accepted',
              value:!known?'ausente':v===true?'true':v===false?'false':'outro',
              type:!known?'undefined':v===null?'null':typeof v
            };
          }else securityDiagnostic={status:'rejected',value:'ausente',type:'undefined'};
        }catch(_){
          securityDiagnostic={status:'unavailable',value:'ausente',type:'undefined'};
        }
      }
      return stop('PIN de saque não confirmado pela plataforma.');
    }
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


def inspect_withdrawal_preflight(page, *, account_start_url=None):
    """Read-only API compatibility discovery in the selected account's site.

    A verified matching origin is necessary but not sufficient for real
    transaction support; this function never sends a withdrawal.
    """
    try:
        origin = urlsplit(str(page.url or ""))
        hosts = (_account_hosts(account_start_url) if account_start_url is not None
                 else ALLOWED_HOSTS)
        if (origin.scheme != "https" or origin.hostname not in hosts or
                origin.port not in (None, 443) or origin.username or origin.password):
            return {"stage": "site", "message": "Navegador fora do domínio autorizado.",
                    "trace": []}
        result = page.evaluate(_JS, {"allowedHosts": sorted(hosts)})
        if not isinstance(result, dict):
            return {"stage": "client",
                    "message": "Não foi possível carregar o cliente autenticado da página.",
                    "trace": []}
        # The page sends only enumerated field presence/type, not the field
        # content (for strings) or any raw API response.
        diag = result.get("pinDiagnostic")
        if isinstance(diag, dict):
            value = diag.get("value")
            kind = diag.get("type")
            if (type(value) is str and type(kind) is str and
                    value in {"true", "false", "ausente", "outro"} and
                    kind in {"undefined", "boolean", "null", "string", "number",
                             "object", "bigint", "symbol", "function"}):
                try:
                    from webui.live_logs import publish
                    publish("Sistema", "[Saque][Diagnóstico] withdrawInfoV2 "
                            f"hasWithdrawPasswd={value}; tipo={kind}; "
                            "consulta somente leitura; saque enviado=não")
                except Exception:
                    pass
        security = result.get("securityDiagnostic")
        if isinstance(security, dict):
            state, val, typ = (security.get("status"), security.get("value"),
                               security.get("type"))
            if (state in {"accepted", "rejected", "unavailable"} and
                    val in {"true", "false", "ausente", "outro"} and
                    typ in {"boolean", "undefined", "null", "string", "number",
                            "object", "bigint", "symbol", "function"}):
                try:
                    from webui.live_logs import publish
                    publish("Sistema", "[Saque][Diagnóstico] security/status "
                            f"resposta={state}; hasWithdrawPasswd={val}; tipo={typ}; "
                            "sem envio de PIN ou saque")
                except Exception:
                    pass
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
