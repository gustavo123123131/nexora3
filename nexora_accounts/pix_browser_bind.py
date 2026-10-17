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

_JS = r"""async ({name, cpf, pin, key, kind}) => {
  const safe = (state, stage, message, code=null) =>
    ({state, stage, message, providerCode: Number.isSafeInteger(Number(code)) ?
        Number(code) : null});
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
  let stage='withdrawInfoV2-antes';
  async function post(route, data) {
    const response = await api.request({
      url: '/api/' + route, method: 'POST',
      ...(data === undefined ? {} : {data})
    }, {merge:false, maxRetryCount:0, cache:{useCache:false}});
    const outer = response?.data ?? response;
    const code = Number(outer?.code);
    if (code !== 1) throw new ProviderError(stage,
      Number.isSafeInteger(code) ? code : null);
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
    const before = await post('finance/certify/withdrawInfoV2');
    const beforeList=list(before);
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
    if (authorization.twoStepVerify) return safe('failed',stage,
      'A plataforma exige verificação adicional. Conclua-a pelo procedimento oficial.');
    if (typeof authorization.key !== 'string' || !authorization.key)
      return safe('failed',stage,'A plataforma não retornou autorização temporária de cadastro.');
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
    if (listed(list(after),key)) return safe('confirmed',stage,
      'Chave Pix confirmada na lista de saques da conta.');
    return safe('accepted_unconfirmed',stage,
      'O cadastro foi aceito pela API, mas a chave ainda não apareceu na listagem.');
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


def bind_in_page(page, *, name: str, cpf: str, pin: str, key: str, kind: str) -> dict:
    """Runs only on the Playwright owner thread, no new navigation."""
    try:
        url = urlsplit(str(page.url or ""))
        if url.scheme != "https" or url.hostname not in OFFICIAL_HOSTS:
            return {"state": "failed", "stage": "site",
                    "message": "Abra a 5gbet na janela Chromium desta conta."}
        result = page.evaluate(_JS, {
            "name": name, "cpf": cpf, "pin": pin, "key": key, "kind": kind,
        })
        if not isinstance(result, dict) or result.get("state") not in FINAL_STATES:
            return {"state": "uncertain", "stage": "client",
                    "message": "Resultado inesperado; confira a plataforma antes de repetir."}
        return {key: result[key] for key in
                ("state", "stage", "message", "providerCode")
                if key in result and
                (key != "providerCode" or type(result[key]) is int) and
                (key != "message" or isinstance(result[key], str))}
    except Exception:
        return {"state": "uncertain", "stage": "browser",
                "message": "Navegador indisponível; confira se a chave foi cadastrada antes de repetir."}
