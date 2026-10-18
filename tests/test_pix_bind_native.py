"""All 5gbet-like traffic is mocked inside a real Chromium browser.

This validates the page SDK dispatch and response interpretation, NOT a
real login, remote API contract, or real financial transaction.
"""
import json
import pytest
from playwright.sync_api import sync_playwright
from nexora_accounts.pix_browser_bind import bind_in_page
from nexora_accounts.pix_operations import PIX_WITHDRAW_PIN

SITE = "https://www.5gbet.com/"
SDK = "https://www.5gbet.com/commonChunk.test.js"
CPF = "52998224725"
PIN = PIX_WITHDRAW_PIN
KEY = "11987654321"

HTML = "<html><body><script src='/commonChunk.test.js' type='module'></script></body></html>"


@pytest.mark.parametrize("scenario,expected,expected_posts", [
    ("ok", "confirmed", 1),
    ("not-listed", "accepted_unconfirmed", 1),
    ("masked-growing", "confirmed", 1),
    ("masked-same-count", "accepted_unconfirmed", 1),
    ("bad-pin", "failed", 0),
    ("security-failure", "failed", 0),
    ("setup-ok", "confirmed", 1),
    ("setup-failure", "failed", 0),
    ("setup-verify-failure", "failed", 0),
    ("bind-failure", "uncertain", 1),
    ("already", "already_registered", 0),
])
def test_real_chromium_routes_native_pix_api_without_external_network(
        scenario, expected, expected_posts):
    js = r"""
      window.__calls=[];
      window.__bound=false;
      export const n = {request:async ({url, method, data},opts) => {
        window.__calls.push({url,method,data});
        const out=(payload,code=1)=>({data:{code,data:payload}});
        if(url.endsWith('/member/user/security/status')){
          return "SCENARIO"==="security-failure" ? out({},401) : out({status:"ok"});
        }
        if(url.endsWith('/member/user/security/modifyWithdrawPass')){
          if("SCENARIO"==="setup-failure") return out({},43000);
          if(data.withdraw_pass!=="387520" || data.secondVerify.type!==0)
            throw Error('mismatched PIN setup payload');
          return out({});
        }
        if(url.endsWith('/member/user/security/verifyWithdrawPass')){
          if("SCENARIO"==="setup-verify-failure") return out({},43001);
          if(data.withdraw_pass!=="387520")
            throw Error('mismatched verification payload');
          return out({});
        }
        if(url.endsWith('/withdrawInfoV2')){
          const list=window.__bound || "SCENARIO"==="already" ?
            [{account:"11987654321"}]:[];
          if(window.__bound && "SCENARIO"==="not-listed")list.length=0;
          if("SCENARIO"==="masked-growing"){
            list.length=0;
            list.push({account:"22888888888"});
            if(window.__bound)list.push({account:"(**) *****-****"});
          }
          if("SCENARIO"==="masked-same-count"){
            list.length=0;
            list.push({account:"22888888888"});
          }
          return out({cpf:"52998224725",withdrawMethodList:[
            {withdrawType:7,subTypes:["PHONE","EMAIL","CPF","EVP"]}],
            withdrawAccounts:list});
        }
        if(url.endsWith('/verifyWithdrawalPasswordV2')){
          if("SCENARIO"==="bad-pin") return out({},41000);
          if(data.withdrawalPassword!=="387520" ||
             data.addWithdrawAccountType!==5) throw Error('wrong PIN');
          return out({key:"test-authorization",twoStepVerify:false});
        }
        if(url.endsWith('/bindalipayV3')){
          if(data.name!=="Maria Silva" || data.account!=="11987654321" ||
             data.extendInfo!=="52998224725" || data.subType!=="PHONE" ||
             data.accountType!==7 || data.withdrawalUUID!=="test-authorization")
              throw Error('payload mismatch');
          if("SCENARIO"==="bind-failure") throw Error('connection closed');
          window.__bound=true;
          return out({});
        }
        throw Error('Unknown route');
      }};
    """.replace("SCENARIO",scenario)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            context = browser.new_context(ignore_https_errors=True)
            def route_handler(route):
                if route.request.url == SITE:
                    route.fulfill(status=200, content_type="text/html", body=HTML)
                elif route.request.url == SDK:
                    route.fulfill(status=200, content_type="text/javascript", body=js)
                else:
                    route.abort()
            context.route("**/*",route_handler)
            page = context.new_page()
            page.goto(SITE,wait_until="domcontentloaded")
            setup = scenario.startswith("setup-") or scenario == "security-failure"
            result=bind_in_page(page, name="Maria Silva",cpf=CPF,pin=PIN,
                                key=KEY,kind="PHONE",initialize_pin=setup)
            assert result["state"]==expected, result
            calls=page.evaluate("""() => window.__calls.map(x=>x.url)""")
            if setup:
                assert calls[0] == "/api/member/user/security/status"
                assert any(row["stage"] == "security/status" for row in result["trace"])
            else:
                assert calls[0] == "/api/finance/certify/withdrawInfoV2"
            if scenario == "security-failure":
                assert result["stage"] == "security/status"
                assert len(calls) == 1
            if scenario == "setup-failure":
                assert result["stage"] == "modifyWithdrawPass"
                assert calls[1].endswith("/modifyWithdrawPass")
                assert len(calls) == 2
            if scenario == "setup-verify-failure":
                assert result["stage"] == "verifyWithdrawPass"
                assert calls[2].endswith("/verifyWithdrawPass")
                assert len(calls) == 3
            if setup and scenario != "security-failure":
                assert calls[1].endswith("/modifyWithdrawPass")
                if scenario != "setup-failure":
                    assert calls[2].endswith("/verifyWithdrawPass")
            else:
                assert not any(x.endswith("modifyWithdrawPass") for x in calls)
            assert calls.count("/api/finance/certify/bindalipayV3")==expected_posts
            if expected_posts:
                assert "/api/finance/certify/verifyWithdrawalPasswordV2" in calls
            if scenario not in (
                "already", "security-failure", "setup-failure", "setup-verify-failure"
            ):
                assert calls.count("/api/finance/certify/verifyWithdrawalPasswordV2") == 1
                request = page.evaluate("""() => window.__calls.find(
                    x => x.url === '/api/finance/certify/verifyWithdrawalPasswordV2'
                )""")
                assert request["method"] == "POST"
                assert request["data"] == {
                    "withdrawalPassword": "387520",
                    "addWithdrawAccountType": 5,
                }
            if scenario == "masked-growing":
                assert result["state"] == "confirmed"
                assert any(row["event"] == "list_growth_confirmed"
                           for row in result["trace"])
            if scenario == "masked-same-count":
                assert result["state"] == "accepted_unconfirmed"
                assert any(row["event"] == "list_no_growth"
                           for row in result["trace"])
            audit=json.dumps(result,ensure_ascii=False)
            for secret in (PIN, CPF, KEY, "Maria Silva"):
                assert secret not in audit
        finally:
            browser.close()


def test_wrong_browser_origin_makes_no_remote_request():
    class FakePage:
        url = "https://not-5gbet.com"
        def evaluate(self,*args):
            raise AssertionError("untrusted page must not execute")
    result=bind_in_page(FakePage(),name="Maria Silva",cpf=CPF,
                        pin=PIN,key=KEY,kind="PHONE")
    assert result["state"]=="failed"
    assert result["stage"]=="site"
