"""Execute the user-supplied 5gbet Pix API flow inside the owning Chromium page.

The endpoint names and request bodies come from the operator's provided
DevTools script. They are NOT a verified published provider contract.
No secrets, full Pix values, JWTs or response bodies leave page.evaluate().
"""
from urllib.parse import urlsplit

OFFICIAL_HOSTS = frozenset(("5gbet.com", "www.5gbet.com"))
FINAL_STATES = frozenset((
    "confirmed", "already_registered", "accepted_unconfirmed", "uncertain",
    "failed", "unsupported",
))

_JS = r"""async ({name, cpf, pin, key, kind, initializePin=false}) => {
  // Keep ONLY bounded, enumerated metadata; never log PIN, Pix, CPF,
  // response bodies, request bodies, tokens or browser exceptions.
  const timeline = [];
  const mark = (step, event, fields={}) => {
    if (timeline.length >= 32) return;
    timeline.push({
      stage:step, event,
      elapsedMs: Number.isSafeInteger(fields.elapsedMs) ? fields.elapsedMs : null,
      providerCode: Number.isSafeInteger(fields.providerCode) ? fields.providerCode : null,
      httpStatus: Number.isSafeInteger(fields.httpStatus) ? fields.httpStatus : null,
      responseShape: fields.responseShape || null
    });
  };
  const codeOf = value => {
    if (value === null || value === undefined || value === '') return null;
    const n = Number(value);
    return Number.isSafeInteger(n) ? n : null;
  };
  const safe = (state, step, message, code=null) => {
    mark(step, 'result', {providerCode:codeOf(code)});
    return {state, stage:step, message,
      providerCode:codeOf(code), trace:timeline};
  };
  if (location.protocol !== 'https:' ||
      !['www.5gbet.com', '5gbet.com'].includes(location.hostname.toLowerCase()))
    return safe('failed', 'site', 'Navegador da conta não está no domínio autorizado.');
  const paths = [
    ...performance.getEntriesByType('resource').map(x => x.name),
    ...Array.from(document.querySelectorAll('script[src]'), x => x.src)
  ];
  const chunk = [...new Set(paths)].find(x => {
    try {
      const u = new URL(x, location.href);
      return u.origin === location.origin &&
        /\/commonChunk\.[^/?]+\.js$/i.test(u.pathname);
    } catch (_) { return false; }
  });
  if (!chunk) return safe('failed', 'client',
    'Cliente HTTP original não localizado nesta página. Abra a área de saque no navegador da conta.');
  let api;
  try {
    api = (await import(chunk)).n;
    if (typeof api?.request !== 'function') throw Error('missing');
  } catch (_) {
    return safe('failed', 'client', 'Cliente HTTP original indisponível.');
  }
  class ProviderError extends Error {
    constructor(stage, code) {
      super('provider');
      this.stage=stage;
      this.code=code;
    }
  }
  let bindStarted=false;
  let bindAccepted=false;
  let stage='security/status';
  async function post(route, data) {
    // stage is one of the pre-defined routes below; never capture payload.
    const started=performance.now();
    mark(stage, 'request_start');
    let response;
    try {
      response = await api.request({
        url: '/api/' + route, method: 'POST',
        ...(data === undefined ? {} : {data})
      }, {merge:false, maxRetryCount:0, cache:{useCache:false}});
    } catch (error) {
      // SDK may reject requests directly (no normal response). Extract
      // ONLY numerical status/code from known locations, never error.message.
      const httpStatus=codeOf(error?.response?.status);
      const code=codeOf(error?.response?.data?.code ??
                        error?.data?.code ?? error?.code);
      mark(stage, 'request_exception', {
        elapsedMs:Math.round(performance.now()-started),
        providerCode:code, httpStatus, responseShape:'exception'
      });
      throw new ProviderError(stage,code);
    }
    const outer = response?.data ?? response;
    const code=codeOf(outer?.code);
    const httpStatus=codeOf(response?.status);
    const elapsedMs=Math.round(performance.now()-started);
    const responseShape=code===null ? 'missing_code' :
      response?.data !== undefined ? 'wrapped_code' : 'direct_code';
    mark(stage, code===1 ? 'api_accepted' : 'api_rejected', {
      providerCode:code, httpStatus, elapsedMs, responseShape
    });
    if (code !== 1) throw new ProviderError(stage,code);
    return outer?.data ?? {};
  }
  const list = data => {
    for (const field of ['accounts', 'accountList', 'withdrawAccounts',
                         'withdrawAccountList']) {
      if (Array.isArray(data?.[field])) return data[field];
    }
    return null;
  };
  const listed = (rows, value) => Array.isArray(rows) && rows.some(row =>
    ['account', 'accountNo', 'account_number', 'addr'].some(field =>
      typeof row?.[field] === 'string' &&
      row[field].replace(/\s/g, '') === value));
  try {
    if (initializePin === true) {
      await post('member/user/security/status');
      stage='modifyWithdrawPass';
      await post('member/user/security/modifyWithdrawPass', {
        withdraw_pass:pin, secondVerify:{type:0}
      });
      stage='verifyWithdrawPass';
      await post('member/user/security/verifyWithdrawPass', {
        withdraw_pass:pin
      });
      mark(stage, 'withdraw_pin_initialized');
    }
    stage='withdrawInfoV2-antes';
    const before = await post('finance/certify/withdrawInfoV2');
    const beforeList=list(before);
    mark(stage, 'list_check', {responseShape:beforeList ? 'list_found' : 'list_missing'});
    if (listed(beforeList,key)) return safe('already_registered',stage,
      'A chave já consta na lista de saques desta conta.');
    const serverCpf=String(before?.cpf || '').replace(/\D/g,'');
    if (serverCpf.length === 11 && serverCpf !== cpf)
      return safe('failed',stage,'O CPF da conta na plataforma é diferente do titular salvo em Contas.');
    const method=before?.withdrawMethodList?.find?.(m => m.withdrawType === 7);
    if (Array.isArray(method?.subTypes) && !method.subTypes.includes(kind))
      return safe('failed',stage,'Este tipo de chave Pix não está habilitado nesta conta.');
    stage='verifyWithdrawalPasswordV2';
    const authorization=await post('finance/certify/verifyWithdrawalPasswordV2', {
      withdrawalPassword:pin, addWithdrawAccountType:5
    });
    if (authorization.twoStepVerify) {
      mark(stage, 'two_step_required');
      return safe('failed',stage,
        'A plataforma exige verificação adicional. Conclua-a pelo procedimento oficial.');
    }
    if (typeof authorization.key !== 'string' || !authorization.key) {
      mark(stage, 'authorization_missing');
      return safe('failed',stage,'A plataforma não retornou autorização temporária de cadastro.');
    }
    mark(stage, 'authorization_received'); // never include the actual key
    stage='bindalipayV3';
    bindStarted=true;
    await post('finance/certify/bindalipayV3', {
      name, account:key, extendInfo:cpf,
      cpf:serverCpf.length === 11 ? serverCpf : '',
      subType:kind, accountType:7,
      subTypeExtend:{bank:'', branchBank:'', companyName:''},
      withdrawalUUID:authorization.key
    });
    bindAccepted=true;
    stage='withdrawInfoV2-depois';
    const after=await post('finance/certify/withdrawInfoV2');
    const afterList=list(after);
    mark(stage, 'list_check', {responseShape:afterList ? 'list_found' : 'list_missing'});
    if (listed(afterList,key)) {
      mark(stage, 'key_matched');
      return safe('confirmed',stage,
        'Chave Pix identificada na lista de saques da conta.');
    }
    // Some provider lists mask the saved key. The operator's working flow
    // therefore also checks whether a new account entry appeared after an
    // accepted bind. Never infer success from merely finding an array.
    if (Array.isArray(beforeList) && Array.isArray(afterList) &&
        afterList.length > beforeList.length) {
      mark(stage, 'list_growth_confirmed');
      return safe('confirmed',stage,
        'API aceitou o cadastro e a listagem ganhou nova entrada; chave individual não identificável.');
    }
    mark(stage,'list_no_growth');
    return safe('accepted_unconfirmed',stage,
      'API aceitou o cadastro, mas a consulta final não identificou a chave nem acréscimo na listagem.');
  } catch (error) {
    const code=error instanceof ProviderError ? error.code : null;
    if (bindAccepted) return safe('accepted_unconfirmed',stage,
      'Cadastro aceito, mas a consulta posterior falhou.', code);
    if (bindStarted) return safe('uncertain',stage,
      'Envio possivelmente efetuado. Confira a lista de saques antes de repetir.',code);
    return safe('failed',stage,
      code !== null ? 'A plataforma recusou a etapa de autorização/cadastro.' :
        'A etapa não foi concluída no navegador selecionado.',code);
  }
}"""


def bind_in_page(page, *, name: str, cpf: str, pin: str, key: str,
                 kind: str, initialize_pin: bool = False) -> dict:
    """Runs only on the Playwright owner thread, no new navigation."""
    try:
        url = urlsplit(str(page.url or ""))
        if url.scheme != "https" or url.hostname not in OFFICIAL_HOSTS:
            return {"state": "failed", "stage": "site",
                    "message": "Abra a 5gbet na janela Chromium desta conta."}
        result = page.evaluate(_JS, {
            "name": name, "cpf": cpf, "pin": pin, "key": key, "kind": kind,
            "initializePin": initialize_pin is True,
        })
        if not isinstance(result, dict) or result.get("state") not in FINAL_STATES:
            return {"state": "uncertain", "stage": "client",
                    "message": "Resultado inesperado; confira a plataforma antes de repetir."}
        output = {key: result[key] for key in
                  ("state", "stage", "message", "providerCode")
                  if key in result and
                  (key != "providerCode" or type(result[key]) is int) and
                  (key != "message" or isinstance(result[key], str))}
        allowed_stages = {"site", "client", "security/status",
                          "modifyWithdrawPass", "verifyWithdrawPass",
                          "withdrawInfoV2-antes",
                          "verifyWithdrawalPasswordV2", "bindalipayV3",
                          "withdrawInfoV2-depois"}
        allowed_events = {"request_start", "request_exception", "api_accepted",
                          "api_rejected", "result", "list_check",
                          "authorization_missing", "authorization_received",
                          "two_step_required", "withdraw_pin_initialized",
                          "key_matched", "list_growth_confirmed",
                          "list_no_growth"}
        allowed_shapes = {"exception", "missing_code", "wrapped_code",
                          "direct_code", "list_found", "list_missing"}
        trace = []
        for item in result.get("trace", [])[:32] if isinstance(result.get("trace"), list) else []:
            if (not isinstance(item, dict) or item.get("stage") not in allowed_stages
                or item.get("event") not in allowed_events):
                continue
            row = {"stage": item["stage"], "event": item["event"]}
            for field in ("providerCode", "httpStatus", "elapsedMs"):
                value = item.get(field)
                lower = 0 if field == "elapsedMs" else -999999
                if type(value) is int and lower <= value <= 999999:
                    row[field] = value
            if item.get("responseShape") in allowed_shapes:
                row["responseShape"] = item["responseShape"]
            trace.append(row)
        output["trace"] = trace
        return output
    except Exception:
        return {"state": "uncertain", "stage": "browser",
                "message": "Navegador indisponível; confira se a chave foi cadastrada antes de repetir."}
