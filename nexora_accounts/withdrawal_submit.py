"""One-shot withdrawal on the already authenticated account's Chromium worker.

This integration is currently supported only for the observed 5GBet web SDK.
The provider's status=1/orderNo means acceptance, not completed payment.
No Playwright form navigation, new browser instance, token copying or retries.
"""
from __future__ import annotations

from pathlib import Path
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from nexora_accounts.local_profiles import LocalProfileStore
from nexora_accounts.pix_key_store import _disk_lock

_LOCK = threading.RLock()
_ACTIVE = frozenset(("queued", "attempting", "accepted", "pending", "indeterminate", "verification_required"))
_STATUSES = _ACTIVE | frozenset(("blocked", "rejected"))
_STAGE = frozenset(("local", "site", "client", "withdrawInfoV2", "gameCenter/gold",
                    "getWithdrawFee", "cashV3", "orderInfo"))
_MESSAGES = frozenset((
    "Solicitação na fila da sessão Chromium.",
    "Tentativa marcada antes do envio; não repetir automaticamente.",
    "Pedido aceito pela plataforma; pagamento não confirmado.",
    "Pedido aceito; consulte o histórico para acompanhar a conclusão.",
    "Pedido em acompanhamento; pagamento não confirmado.",
    "A plataforma exige verificação adicional para este pedido.",
    "Resposta ambígua após envio. Verifique o histórico antes de tentar novamente.",
    "Envio não iniciado: PIN de saque não salvo no cofre Windows.",
    "Envio não iniciado: conta ou sessão divergente.",
    "Envio não iniciado: a sessão Chromium não está disponível.",
    "Envio não iniciado: domínio não autorizado.",
    "Envio não iniciado: cliente web autenticado indisponível.",
    "Envio não iniciado: canal de saque desabilitado.",
    "Envio não iniciado: PIN não confirmado no perfil remoto.",
    "Envio não iniciado: destino único não confirmado.",
    "Envio não iniciado: destino remoto não é Pix ou não tem ID.",
    "Envio não iniciado: saldo, limite ou taxa inválidos.",
    "Envio não iniciado: consulta remota rejeitada.",
    "Envio não iniciado: a fila da sessão está ocupada.",
))

class WithdrawalSubmissionError(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat()


def _uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _path():
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_STATE_HOME")
    return (Path(base) if base else Path.home()/".local"/"share") / "Nexora" / "withdrawal_submissions.json"


def _log(profile_id, op_id, message):
    try:
        from webui.live_logs import publish
        publish("Sistema", f"[Saque] Conta #{int(profile_id)} pedido={str(op_id)[:8]} {message}")
    except Exception:
        pass


class WithdrawalSubmissionStore:
    def __init__(self, path=None, *, profiles=None):
        self.path=Path(path) if path else _path()
        self.profiles=profiles or LocalProfileStore()

    def _read(self):
        if self.path.is_symlink():
            raise WithdrawalSubmissionError("Histórico de pedidos inválido.")
        if not self.path.exists():
            return {"version":1,"orders":[]}
        try:
            data=json.loads(self.path.read_text(encoding="utf-8"))
            if (type(data) is not dict or data.get("version")!=1 or
                type(data.get("orders")) is not list or len(data["orders"])>10000):
                raise ValueError("schema")
            for order in data["orders"]:
                if (type(order) is not dict or not _uuid(order.get("operationId")) or
                    not _uuid(order.get("accountId")) or order.get("status") not in _STATUSES):
                    raise ValueError("item")
            return data
        except (OSError, ValueError, UnicodeError, TypeError) as exc:
            raise WithdrawalSubmissionError("Histórico de pedidos inválido.") from exc

    def _write(self,data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        pending=None
        try:
            with tempfile.NamedTemporaryFile(mode="w",encoding="utf-8",dir=self.path.parent,
                prefix=".withdraw-orders-",suffix=".tmp",delete=False) as fd:
                pending=fd.name
                json.dump(data,fd,ensure_ascii=False,indent=2)
                fd.flush()
                os.fsync(fd.fileno())
            os.chmod(pending,0o600)
            os.replace(pending,self.path)
        finally:
            if pending and os.path.exists(pending):
                os.unlink(pending)

    def list(self,profile_id):
        if type(profile_id) is not int or profile_id<1:
            raise WithdrawalSubmissionError("Conta inválida.")
        with _LOCK,_disk_lock(self.path):
            return {"orders":[dict(x) for x in reversed(self._read()["orders"])
                              if x.get("profileId")==profile_id][:60]}

    def reserve(self,*,profile_id,account_id,run_id):
        if type(profile_id) is not int or profile_id<1 or not _uuid(account_id) or not _uuid(run_id):
            raise WithdrawalSubmissionError("Conta ou sessão inválida.")
        detail=self.profiles.public_detail(profile_id)
        if detail.get("accountId")!=account_id or detail.get("lastSessionId")!=run_id:
            raise WithdrawalSubmissionError("Conta ou sessão mudou; atualize Operações.")
        with _LOCK,_disk_lock(self.path):
            data=self._read()
            for old in reversed(data["orders"]):
                if old["accountId"]==account_id and old["status"] in _ACTIVE:
                    return {"operation":dict(old),"reused":True}
            now=_now()
            op={"operationId":str(uuid4()),"profileId":profile_id,"accountId":account_id,
                "runId":run_id,"status":"queued","stage":"local",
                "message":"Solicitação na fila da sessão Chromium.","submissionSent":False,
                "orderNo":None,"amount":None,"fee":None,"providerStatus":None,
                "createdAt":now,"updatedAt":now}
            data["orders"].append(op)
            self._write(data)
            return {"operation":dict(op),"reused":False}

    def get(self,op_id,*,profile_id,account_id,run_id):
        with _LOCK,_disk_lock(self.path):
            for row in self._read()["orders"]:
                if (row.get("operationId")==op_id and row.get("profileId")==profile_id
                    and row.get("accountId")==account_id and row.get("runId")==run_id):
                    return dict(row)
        raise WithdrawalSubmissionError("Pedido não pertence à conta e sessão informadas.")

    def refresh_status(self,op_id,*,profile_id,account_id,run_id,provider_status):
        if type(provider_status) is not int or provider_status<0 or provider_status>1000:
            raise WithdrawalSubmissionError("Estado remoto inválido.")
        with _LOCK,_disk_lock(self.path):
            data=self._read()
            row=next((x for x in data["orders"] if x.get("operationId")==op_id
                and x.get("profileId")==profile_id and x.get("accountId")==account_id
                and x.get("runId")==run_id),None)
            if row is None or row["status"] not in {"accepted","pending","verification_required"}:
                raise WithdrawalSubmissionError("Pedido não disponível para consulta.")
            row.update(providerStatus=provider_status,stage="orderInfo",status="pending",
                message="Pedido em acompanhamento; pagamento não confirmado.",updatedAt=_now())
            self._write(data)
            return dict(row)

    def update(self,op_id,*,profile_id,account_id,run_id,status,stage,message,**details):
        if status not in _STATUSES or stage not in _STAGE or message not in _MESSAGES:
            raise WithdrawalSubmissionError("Resultado de saque inválido.")
        with _LOCK,_disk_lock(self.path):
            data=self._read()
            row=next((x for x in data["orders"] if x.get("operationId")==op_id
                      and x.get("profileId")==profile_id and x.get("accountId")==account_id
                      and x.get("runId")==run_id),None)
            if row is None: raise WithdrawalSubmissionError("Pedido não encontrado.")
            if row["status"] not in {"queued","attempting"}:
                return dict(row)
            # A request may have crossed the network once status is attempting.
            # Never demote that to a retryable blocked/rejected state.
            if row["status"]=="attempting" and status in {"blocked","rejected"}:
                # Only a trusted synchronous preflight result with no cashV3
                # attempt can unlock the account. A lost response remains locked.
                early = stage in {"local", "site", "client", "withdrawInfoV2",
                                  "gameCenter/gold", "getWithdrawFee"}
                if not early:
                    status="indeterminate"
                    message="Resposta ambígua após envio. Verifique o histórico antes de tentar novamente."
                else:
                    row["submissionSent"]=False
            row.update(status=status,stage=stage,message=message,updatedAt=_now(),
                       submissionSent=(status=="attempting" or
                        row.get("submissionSent") and status not in {"blocked", "rejected"}))
            if type(details.get("orderNo")) is str and 0<len(details["orderNo"])<=100:
                row["orderNo"]=details["orderNo"]
            for key in ("amount","fee"):
                value=details.get(key)
                if type(value) in (int,float) and 0<=value<=10000000:
                    row[key]=value
            if type(details.get("providerStatus")) is int and 0<=details["providerStatus"]<=1000:
                row["providerStatus"]=details["providerStatus"]
            self._write(data)
            return dict(row)


# This code executes inside the existing selected Chromium page and uses only
# the browser's authenticated 5GBet SDK. It never logs auth credentials or PINs.
_JS = r"""async ({pin}) => {
  const done=(status,stage,message,more={})=>({status,stage,message,...more});
  const allow=['www.5gbet.com'];
  if(location.protocol!=='https:' || !allow.includes(location.hostname.toLowerCase()))
    return done('blocked','site','Envio não iniciado: domínio não autorizado.');
  if(typeof pin!=='string' || !/^\d{6}$/.test(pin))
    return done('blocked','local','Envio não iniciado: PIN de saque não salvo no cofre Windows.');
  let dispatched=false,phase='client';
  const val=x=>x===null||x===undefined||x===''||!Number.isFinite(Number(x))?null:Number(x);
  const resources=[...performance.getEntriesByType('resource').map(x=>x.name),
    ...Array.from(document.querySelectorAll('script[src],link[rel="modulepreload"][href]')).map(x=>x.src||x.href)];
  const candidates=[...new Set(resources)].filter(x=>{
    try { const u=new URL(x,location.href);
      return u.origin===location.origin && /\/(?:commonChunk|common-chunk)\.[a-z0-9_-]+\.m?js$/i.test(u.pathname);
    }catch(_){return false;}
  }).slice(0,10);
  let api=null;
  for(const url of candidates){
    try{
      const mod=await import(url);
      const found=Object.values(mod).filter(x=>x?.name==='lobby' &&
        typeof x?.request==='function' && typeof x?.setupCustomConfig==='function' &&
        typeof x?.listenLoginStatus==='function');
      if(found.length!==1 || api)return done('blocked','client','Envio não iniciado: cliente web autenticado indisponível.');
      api=found[0];
    }catch(_){/* Try next chunk. */}
  }
  if(!api)return done('blocked','client','Envio não iniciado: cliente web autenticado indisponível.');
  async function post(path,data){
    const res=await api.request({url:'/api/'+path,method:'POST',timeout:18000,
      ...(data===undefined?{}:{data})},{maxRetryCount:0,
        cache:{useCache:false,setCache:false,useCacheOnError:()=>false},
        error:{action:'silent',report:false,checkResCode:true}});
    if(val(res?.status)!==200 || val(res?.data?.code)!==1)throw Error('REJECTED');
    return res.data.data || {};
  }
  try{
    phase='withdrawInfoV2';
    const info=await post('finance/certify/withdrawInfoV2');
    if(info.isEnableChannel===false || info.withdrawClose?.isEnableChannel===false)
      return done('blocked',phase,'Envio não iniciado: canal de saque desabilitado.');
    if(info.hasWithdrawPasswd!==true)
      return done('blocked',phase,'Envio não iniciado: PIN não confirmado no perfil remoto.');
    const accounts=Array.isArray(info.accounts)&&info.accounts.length?info.accounts:
      Array.isArray(info.accountsV2)?info.accountsV2:[];
    if(accounts.length!==1)return done('blocked',phase,'Envio não iniciado: destino único não confirmado.');
    const dest=accounts[0],kind=val(dest?.accountType);
    if(dest?.id===null || dest?.id===undefined || String(dest?.id)==='' || kind!==7)
      return done('blocked',phase,'Envio não iniciado: destino remoto não é Pix ou não tem ID.');
    phase='gameCenter/gold';
    const wallet=await post('gameCenter/gold');
    const balance=val(typeof wallet==='number'||typeof wallet==='string'?wallet:
      wallet?.game_gold??wallet?.gold??wallet?.balance??wallet?.gameGold);
    const min=val(info.withdrawMin),max=val(info.withdrawMax);
    const amount=balance===null||max===null?null:Math.min(Math.floor(Math.max(0,balance)),max);
    if(!Number.isSafeInteger(amount)||!Number.isFinite(min)||amount<min || amount<=0 ||
       !Number.isFinite(max)||max<min)
      return done('blocked',phase,'Envio não iniciado: saldo, limite ou taxa inválidos.');
    phase='getWithdrawFee';
    const quote=await post('finance/certify/getWithdrawFee',
      {withdrawTypeId:kind,amount});
    const fee=val(quote?.fee??quote?.withdrawFee);
    if(fee===null||fee<0||fee>amount)
      return done('blocked',phase,'Envio não iniciado: saldo, limite ou taxa inválidos.');
    // No separate navigation, no repeated request, no storage of destination/PIN.
    phase='cashV3';
    dispatched=true;
    const request={id:String(dest.id),money:String(amount),isWallet:false,
      passwd:pin,accountType:kind,twoStepVerifyInfo:null,
      withdrawResetBonus:info.auditModeInfo?.withdrawResetBonus,authVerify:{}};
    const result=await post('finance/certify/cashV3',request);
    const order=result?.orderNo??result?.order_no;
    const orderNo=(typeof order==='string'||typeof order==='number')?String(order):null;
    if(!orderNo||orderNo.length>100)
      return done('indeterminate',phase,
        'Resposta ambígua após envio. Verifique o histórico antes de tentar novamente.');
    if(result.twoStepVerify)return done('verification_required',phase,
      'A plataforma exige verificação adicional para este pedido.',{orderNo,amount,fee});
    phase='orderInfo';
    try{
      const status=await post('finance/certify/orderInfo',{order_no:orderNo});
      const providerStatus=val(status.status);
      return done('pending',phase,
        'Pedido em acompanhamento; pagamento não confirmado.',
        {orderNo,amount,fee,providerStatus:Number.isSafeInteger(providerStatus)?providerStatus:null});
    }catch(_){return done('accepted',phase,
      'Pedido aceito; consulte o histórico para acompanhar a conclusão.',{orderNo,amount,fee});}
  }catch(_){
    return dispatched ? done('indeterminate',phase,
      'Resposta ambígua após envio. Verifique o histórico antes de tentar novamente.'):
      done('blocked',phase,'Envio não iniciado: consulta remota rejeitada.');
  }
}"""


def send_in_page(page, pin):
    """Return only safe metadata; never leak API bodies, PIN or destination."""
    try:
        origin=urlsplit(str(page.url or ""))
        if origin.scheme!="https" or origin.hostname!="www.5gbet.com":
            return {"status":"blocked","stage":"site",
                    "message":"Envio não iniciado: domínio não autorizado."}
        result=page.evaluate(_JS,{"pin":pin})
        if not isinstance(result,dict):
            raise ValueError("shape")
        status=result.get("status")
        stage=result.get("stage")
        msg=result.get("message")
        if status not in _STATUSES or stage not in _STAGE or msg not in _MESSAGES:
            raise ValueError("invalid metadata")
        safe={"status":status,"stage":stage,"message":msg}
        if status in {"accepted","pending","verification_required"}:
            if type(result.get("orderNo")) is str and 0<len(result["orderNo"])<=100:
                safe["orderNo"]=result["orderNo"]
            for key in ("amount","fee","providerStatus"):
                if key in result:safe[key]=result[key]
        return safe
    except Exception:
        return {"status":"indeterminate","stage":"cashV3",
                "message":"Resposta ambígua após envio. Verifique o histórico antes de tentar novamente."}

_STATUS_JS = r"""async ({orderNo}) => {
  if(location.protocol!=='https:'||location.hostname.toLowerCase()!=='www.5gbet.com')
    return {ok:false};
  const resources=[...performance.getEntriesByType('resource').map(x=>x.name),
    ...Array.from(document.querySelectorAll('script[src]'),x=>x.src)];
  const candidates=[...new Set(resources)].filter(x=>{
    try {const u=new URL(x,location.href);return u.origin===location.origin &&
      /\/(?:commonChunk|common-chunk)\.[a-z0-9_-]+\.m?js$/i.test(u.pathname);
    }catch(_){return false;}
  });
  try{
    for(const url of candidates){
      const mod=await import(url);
      const clients=Object.values(mod).filter(x=>x?.name==='lobby' &&
        typeof x?.request==='function' && typeof x?.setupCustomConfig==='function' &&
        typeof x?.listenLoginStatus==='function');
      if(clients.length!==1)continue;
      const res=await clients[0].request({url:'/api/finance/certify/orderInfo',
        method:'POST',timeout:18000,data:{order_no:orderNo}},
        {maxRetryCount:0,cache:{useCache:false,setCache:false}});
      const status=Number(res?.data?.data?.status);
      return {ok:res?.status===200 && res?.data?.code===1 &&
        Number.isSafeInteger(status) && status>=0 && status<=1000,
        providerStatus:status};
    }
  }catch(_){}
  return {ok:false};
}"""


def refresh_order_in_page(page, order_no):
    try:
        origin=urlsplit(str(page.url or ""))
        if origin.scheme!="https" or origin.hostname!="www.5gbet.com":return None
        if type(order_no) is not str or not 0<len(order_no)<=100:return None
        result=page.evaluate(_STATUS_JS,{"orderNo":order_no})
        if type(result) is dict and result.get("ok") is True and \
            type(result.get("providerStatus")) is int:
            return result["providerStatus"]
    except Exception:
        pass
    return None
