"""Read-only automatic /hall/api discovery on the selected account's site."""
import json
import shutil
import subprocess

import pytest

from nexora_accounts.withdrawal_browser import (
    _JS, _account_hosts, inspect_withdrawal_preflight,
)


def test_saved_site_host_variants_and_non_https_rejected():
    assert _account_hosts("https://80win.net/register") == frozenset(
        ("80win.net", "www.80win.net"))
    assert _account_hosts("https://www.5gbet.com/") == frozenset(
        ("5gbet.com", "www.5gbet.com"))
    for url in ("http://80win.net", "https://user:pw@80win.net",
                "https://80win.net:8443", "data:text/html,foo", ""):
        assert not _account_hosts(url)


def test_only_own_account_origin_can_be_inspected():
    class Page:
        url = "https://www.80win.net/account"
        def __init__(self):
            self.calls = []
        def evaluate(self, script, params):
            self.calls.append((script, params))
            return {"stage": "contract",
                    "message": "Consultas de saque válidas; envio financeiro requer contrato autorizado.",
                    "trace": []}

    page = Page()
    result = inspect_withdrawal_preflight(page, account_start_url="https://80win.net/home")
    assert result["stage"] == "contract"
    assert len(page.calls) == 1
    assert page.calls[0][1] == {"allowedHosts": ["80win.net", "www.80win.net"]}
    assert "cashV3" not in page.calls[0][0]
    other = inspect_withdrawal_preflight(page, account_start_url="https://5gbet.com")
    assert other["stage"] == "site"
    assert len(page.calls) == 1


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js unavailable")
def test_80win_like_site_api_is_detected_read_only_with_fake_sdk():
    script = _JS.replace("await import(chunk)", "await loadModule(chunk)")
    harness = r'''(async () => {
      global.location={protocol:"https:",hostname:"www.80win.net",
        origin:"https://www.80win.net",href:"https://www.80win.net/account"};
      global.performance={now:()=>0,getEntriesByType:()=>
        [{name:"https://www.80win.net/commonChunk.mock.js"}]};
      global.document={querySelectorAll:()=>[]};
      const requests=[];
      global.loadModule=async()=>({n:{request:async({url,data})=>{
        requests.push(url);
        let dataReply;
        if(url.endsWith("withdrawInfoV2"))dataReply={
          hasWithdrawPasswd:true,withdrawMin:5,withdrawMax:1000,
          accounts:[{id:"remote-1",withdrawTypeId:7}]};
        else if(url.endsWith("gameCenter/gold"))dataReply={
          userInfos:{game_gold:"26"}};
        else if(url.endsWith("getWithdrawFee"))dataReply={withdrawFee:0};
        else throw Error("Unexpected route: "+url);
        return {status:200,data:{code:1,data:dataReply}};
      }}});
      const result=await eval("("+process.argv[1]+")")({
        allowedHosts:["80win.net","www.80win.net"]});
      process.stdout.write(JSON.stringify({result,requests}));
    })().catch(err=>{console.error(err);process.exit(1)});'''
    done = subprocess.run(["node", "-e", harness, script],
                          text=True, capture_output=True, timeout=15)
    assert done.returncode == 0, done.stderr
    data = json.loads(done.stdout)
    assert data["result"]["stage"] == "contract", data
    assert data["requests"] == [
        "/api/finance/certify/withdrawInfoV2",
        "/api/gameCenter/gold",
        "/api/finance/certify/getWithdrawFee",
    ]
    assert all("cashV3" not in url for url in data["requests"])


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js unavailable")
@pytest.mark.parametrize("scenario,expected_status,expected_value,count", [
    ("missing_status_field", "accepted", "ausente", 2),
    ("security_has_true", "accepted", "true", 2),
    ("security_reject", "rejected", "ausente", 2),
    ("security_network_fail", "unavailable", "ausente", 2),
    ("explicit_false", None, None, 1),
])
def test_missing_withdraw_pin_field_probes_security_status_safely(
        scenario, expected_status, expected_value, count):
    script = _JS.replace("await import(chunk)", "await loadModule(chunk)")
    harness = r'''(async()=>{
      const scenario=process.argv[2];
      global.location={protocol:"https:",hostname:"www.5gbet.com",
        origin:"https://www.5gbet.com",href:"https://www.5gbet.com/account"};
      global.performance={now:()=>0,getEntriesByType:()=>
        [{name:"https://www.5gbet.com/commonChunk.mock.js"}]};
      global.document={querySelectorAll:()=>[]};
      const requests=[];
      global.loadModule=async()=>({n:{request:async({url,data})=>{
        requests.push({url,hasPin:!!data && ("withdraw_pass" in data)});
        if(url.endsWith("/withdrawInfoV2")){
          return {status:200,data:{code:1,data:
            scenario==="explicit_false"?{hasWithdrawPasswd:false}:{}}};
        }
        if(url.endsWith("/member/user/security/status")){
          if(scenario==="security_network_fail")throw Error("PRIVATE_TOKEN");
          if(scenario==="security_reject")return {status:200,data:{code:2,data:{}}};
          return {status:200,data:{code:1,data:
            scenario==="security_has_true"?
            {hasWithdrawPasswd:true,secret:"PRIVATE_TOKEN"}:{secret:"PRIVATE_TOKEN"}}};
        }
        throw Error("Unexpected route "+url);
      }}});
      const result=await eval("("+process.argv[1]+")")({
        allowedHosts:["www.5gbet.com","5gbet.com"]});
      process.stdout.write(JSON.stringify({result,requests}));
    })().catch(err=>{console.error(err);process.exit(1)});'''
    done=subprocess.run(["node", "-e", harness, script, scenario],
                        text=True, capture_output=True, timeout=15)
    assert done.returncode == 0, done.stderr
    response=json.loads(done.stdout)
    result=response["result"]
    assert result["stage"] == "withdrawInfoV2"
    assert len(response["requests"]) == count
    assert response["requests"][0]["url"].endswith("/withdrawInfoV2")
    assert not any(x["hasPin"] for x in response["requests"])
    assert not any("cashV3" in x["url"] for x in response["requests"])
    if count == 2:
        assert response["requests"][1]["url"].endswith("/member/user/security/status")
        assert result["securityDiagnostic"]["status"] == expected_status
        assert result["securityDiagnostic"]["value"] == expected_value
    else:
        assert "securityDiagnostic" not in result
    assert "PRIVATE_TOKEN" not in json.dumps(response)


def test_official_pin_verification_is_account_scoped():
    class Page:
        url = "https://5gbet.com/account"
        def __init__(self):
            self.params = []
        def evaluate(self, src, params):
            self.params.append(params)
            return {"stage": "verifyWithdrawPass",
                    "message": "PIN de saque não confirmado pela plataforma.",
                    "trace": []}

    page = Page()
    for start in ("https://5gbet.com/", "https://www.5gbet.com/"):
        inspect_withdrawal_preflight(page, account_start_url=start,
                                     verify_pin="123456")
        assert page.params[-1]["pin"] == "123456"
    inspect_withdrawal_preflight(page, account_start_url="https://80win.net/")
    assert len(page.params) == 2
    # Generic discovery can never receive a PIN even when the page is valid.
    page.url = "https://www.80win.net/"
    inspect_withdrawal_preflight(page, account_start_url="https://80win.net/",
                                 verify_pin="123456")
    assert "pin" not in page.params[-1]


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js unavailable")
@pytest.mark.parametrize("scenario,expected_stage,expected_state,expected_routes", [
    ("valid", "contract", "api_accepted", 5),
    ("wrong_pin", "verifyWithdrawPass", "rejected", 3),
    ("two_step", "verifyWithdrawPass", "additional_verification", 3),
    ("explicit_false", "withdrawInfoV2", None, 1),
    ("without_pin", "withdrawInfoV2", None, 2),
])
def test_remote_pin_check_read_only_never_submits_cash(
        scenario,expected_stage,expected_state,expected_routes):
    script=_JS.replace("await import(chunk)", "await loadModule(chunk)")
    harness=r'''(async()=>{
      const scenario=process.argv[2];
      global.location={protocol:"https:",hostname:"www.5gbet.com",
        origin:"https://www.5gbet.com",href:"https://www.5gbet.com/account"};
      global.performance={now:()=>0,getEntriesByType:()=>
        [{name:"https://www.5gbet.com/commonChunk.mock.js"}]};
      global.document={querySelectorAll:()=>[]};
      const calls=[];
      global.loadModule=async()=>({n:{request:async({url,data})=>{
        calls.push({url,pinPayload:!!data&&("withdraw_pass" in data)});
        if(url.endsWith("/withdrawInfoV2")){
          const data=scenario==="explicit_false"?{hasWithdrawPasswd:false}:{
            withdrawMin:5,withdrawMax:100,accounts:[{id:"dest1",withdrawTypeId:7}]};
          return {status:200,data:{code:1,data}};
        }
        if(url.endsWith("/member/user/security/status"))
          return {status:200,data:{code:1,data:{}}};
        if(url.endsWith("/member/user/security/verifyWithdrawPass")){
          if(data?.withdraw_pass!=="123456")throw Error("wrong verification argument");
          if(scenario==="wrong_pin")return {status:200,data:{code:401,data:{}}};
          return {status:200,data:{code:1,data:
            scenario==="two_step"?{twoStepVerify:true}:{}}};
        }
        if(url.endsWith("/gameCenter/gold"))
          return {status:200,data:{code:1,data:{userInfos:{game_gold:26}}}};
        if(url.endsWith("/getWithdrawFee"))
          return {status:200,data:{code:1,data:{withdrawFee:0}}};
        throw Error("unexpected: "+url);
      }}});
      const fn=eval("("+process.argv[1]+")");
      const result=await fn({allowedHosts:["5gbet.com","www.5gbet.com"],
        ...(scenario==="without_pin"?{}:{pin:"123456"})});
      process.stdout.write(JSON.stringify({result,calls}));
    })().catch(e=>{console.error(e);process.exit(1)});'''
    proc=subprocess.run(["node","-e",harness,script,scenario],text=True,
                        capture_output=True,timeout=15)
    assert proc.returncode==0,proc.stderr
    out=json.loads(proc.stdout)
    assert out["result"]["stage"] == expected_stage
    assert len(out["calls"]) == expected_routes
    assert all("cashV3" not in c["url"] for c in out["calls"])
    assert ("pinVerification" in out["result"]) == (expected_state is not None)
    if expected_state is not None:
        assert out["result"]["pinVerification"] == expected_state
        assert sum(c["pinPayload"] for c in out["calls"]) == 1
    else:
        assert not any(c["pinPayload"] for c in out["calls"])
    # Neither status response bodies nor the PIN may escape the browser.
    assert "123456" not in json.dumps(out)
