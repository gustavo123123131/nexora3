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

_JS = r"""async ({allowedHosts, pin=null}={}) => {
  const trace=[];
  let stage='client';
  const mark=(event,status=null,code=null,elapsedMs=null)=>{
    if(trace.length<40)trace.push({stage,event,
      httpStatus:Number.isSafeInteger(status)?status:null,
      providerCode:Number.isSafeInteger(code)?code:null,
      elapsedMs:Number.isSafeInteger(elapsedMs)?elapsedMs:null});
  };
  // Only enumerated PIN-field metadata may leave this page; no raw payloads.
  let pinDiagnostic=null, securityDiagnostic=null, pinVerification=null;
  const stop=(message)=>{mark('result');return {
    stage,message,trace,
    ...(pinDiagnostic===null?{}:{pinDiagnostic}),
    ...(securityDiagnostic===null?{}:{securityDiagnostic}),
    ...(pinVerification===null?{}:{pinVerification})
  };};
  const allowed=Array.isArray(allowedHosts)&&allowedHosts.length?
    allowedHosts:['www.5gbet.com','5gbet.com'];
  mark('preflight_started');
  if(location.protocol!=='https:' ||
     !allowed.includes(location.hostname.toLowerCase())){
    stage='site';mark('origin_rejected');
    return stop('Navegador fora do domínio autorizado.');
  }
  mark('origin_accepted');
  const urls=[...performance.getEntriesByType('resource').map(x=>x.name),
    ...Array.from(document.querySelectorAll('script[src]'),x=>x.src)];
  const chunk=[...new Set(urls)].find(x=>{
    try {
      const u=new URL(x,location.href);
      return u.origin===location.origin &&
        /\/commonChunk\.[^/?]+\.js$/i.test(u.pathname);
    }catch(_){return false;}
  });
  if(!chunk){mark('sdk_missing');return stop('Não foi possível carregar o cliente autenticado da página.');}
  mark('sdk_candidate_found');
  let sdk;
  try{
    sdk=(await import(chunk)).n;
    if(typeof sdk?.request!=='function')throw Error('unavailable');
  }catch(_){mark('sdk_missing');return stop('Não foi possível carregar o cliente autenticado da página.');}
  mark('sdk_ready');
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
    mark(!present?'pin_field_absent':raw===true?'pin_field_true':
      raw===false?'pin_field_false':'pin_field_other');
    if(info.hasWithdrawPasswd!==true){
      // The withdrawal endpoint sometimes omits this flag. Inspect the
      // independently exposed security status without sending a PIN or payout.
      if(!present){
        stage='security/status';
        mark('request_start');
        const statusStarted=performance.now();
        try{
          const res=await sdk.request({url:'/api/member/user/security/status',
            method:'POST',timeout:18000},
            {merge:false,maxRetryCount:0,cache:{useCache:false}});
          const statusCode=Number(res?.data?.code);
          const statusHttp=Number(res?.status);
          mark(statusCode===1?'api_accepted':'api_rejected',
            Number.isSafeInteger(statusHttp)?statusHttp:null,
            Number.isSafeInteger(statusCode)?statusCode:null,
            Math.round(performance.now()-statusStarted));
          if(statusCode===1){
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
          mark(securityDiagnostic.value==='ausente'?'pin_field_absent':'pin_field_present');
        }catch(_){
          mark('request_exception',null,null,Math.round(performance.now()-statusStarted));
          securityDiagnostic={status:'unavailable',value:'ausente',type:'undefined'};
        }
        stage='withdrawInfoV2';
      }
      // Only perform credential verification within the authenticated,
      // explicit 5GBet account origin, never on an automatically found site.
      if(!present && typeof pin==='string' && /^\d{6}$/.test(pin) &&
         ['www.5gbet.com','5gbet.com'].includes(location.hostname.toLowerCase())){
        stage='verifyWithdrawPass';
        mark('pin_check_eligible');
        try{
          const check=await post('member/user/security/verifyWithdrawPass',
            {withdraw_pass:pin});
          if(check?.twoStepVerify || check?.verified===false ||
             check?.success===false){
            pinVerification='additional_verification';
            mark('pin_two_step_required');
            return stop('PIN de saque não confirmado pela plataforma.');
          }
          pinVerification='api_accepted';
          mark('pin_check_accepted');
          stage='withdrawInfoV2';
        }catch(_){
          pinVerification='rejected';
          mark('pin_check_rejected');
          return stop('PIN de saque não confirmado pela plataforma.');
        }
      }else{
        stage='verifyWithdrawPass';
        mark('pin_check_skipped');
        stage='withdrawInfoV2';
        return stop('PIN de saque não confirmado pela plataforma.');
      }
    }
    // The source research only confirms "accounts" for withdrawal destinations.
    if(!Array.isArray(info.accounts) || !info.accounts.length){
      mark('destination_missing');
      return stop('Nenhum destino de saque vinculado nesta conta.');
    }
    if(info.accounts.length!==1){
      mark('destination_multiple');
      return stop('Mais de um destino remoto: seleção precisa de contrato validado.');
    }
    mark('destination_single');
    const target=info.accounts[0];
    if(!target || (typeof target.id!=='string' && typeof target.id!=='number')
      || !String(target.id).trim()){
      mark('destination_id_missing');
      return stop('Conta de destino sem identificador remoto comprovado.');
    }
    mark('destination_id_present');
    const min=Number(info.withdrawMin), max=Number(info.withdrawMax);
    if(!Number.isFinite(min)||!Number.isFinite(max)||min<=0||max<min){
      mark('limits_invalid');
      return stop('Limites de saque não foram identificados.');
    }
    mark('limits_valid');
    stage='gameCenter/gold';
    const wallet=await post('gameCenter/gold');
    const rawBalance=wallet?.userInfos?.game_gold;
    if(rawBalance===null || rawBalance===undefined ||
       !Number.isFinite(Number(rawBalance)) || Number(rawBalance)<0){
      mark('balance_unavailable');
      return stop('Saldo realmente sacável não foi identificado em userInfos.game_gold.');
    }
    mark('balance_available');
    const amount=Math.min(Math.max(0,Math.floor(Number(rawBalance))),max);
    if(!Number.isFinite(amount)||amount<min){
      mark('balance_below_minimum');
      return stop('Saldo disponível inferior ao mínimo permitido.');
    }
    mark('balance_sufficient');
    const method=target?.withdrawTypeId;
    if(!Number.isSafeInteger(method)||method<0){
      mark('method_missing');
      return stop('Tipo de saque remoto não identificado.');
    }
    mark('method_identified');
    stage='getWithdrawFee';
    const quote=await post('finance/certify/getWithdrawFee',{
      withdrawTypeId:method,amount,time:Math.floor(Date.now()/1000)
    });
    const fee=quote?.withdrawFee ?? quote?.fee;
    if(fee===null||fee===undefined||!Number.isFinite(Number(fee)) ||
       Number(fee)<0||Number(fee)>amount){
      mark('fee_invalid');
      return stop('Taxa do saque não foi confirmada.');
    }
    mark('fee_valid');
    stage='contract';
    mark('preflight_complete');
    return stop('Consultas de saque válidas; envio financeiro requer contrato autorizado.');
  }catch(_){
    if(stage==='withdrawInfoV2')return stop('Não foi possível consultar a conta de saque.');
    if(stage==='gameCenter/gold')return stop('Consulta de saldo falhou ou não é suportada.');
    if(stage==='getWithdrawFee')return stop('Consulta de taxa falhou ou não é suportada.');
    return stop('Pré-requisitos não verificados; nenhum saque enviado.');
  }
}"""


def inspect_withdrawal_preflight(page, *, account_start_url=None, verify_pin=None):
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
        params = {"allowedHosts": sorted(hosts)}
        # Never pass a PIN to a generic platform. Both the account's saved
        # origin and current Chromium page must be a documented official host.
        saved = urlsplit(str(account_start_url or ""))
        if (saved.scheme == "https" and saved.hostname in ALLOWED_HOSTS and
                origin.hostname in ALLOWED_HOSTS and
                type(verify_pin) is str and len(verify_pin) == 6 and
                verify_pin.isascii() and verify_pin.isdigit()):
            params["pin"] = verify_pin
        result = page.evaluate(_JS, params)
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
        pin_verification = result.get("pinVerification")
        if pin_verification in {"api_accepted", "rejected", "additional_verification"}:
            try:
                from webui.live_logs import publish
                publish("Sistema", "[Saque][Diagnóstico] verifyWithdrawPass "
                        f"resultado={pin_verification}; sem exibição de PIN; "
                        "saque enviado=não")
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
