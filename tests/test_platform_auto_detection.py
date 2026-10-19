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
