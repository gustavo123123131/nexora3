"""Offline Chromium regression tests for the first-time withdrawal PIN path.

All provider requests are intercepted locally: no external accounts or funds.
"""
import json

import pytest
from playwright.sync_api import sync_playwright

from nexora_accounts.pix_browser_bind import bind_in_page
from nexora_accounts.pix_operations import PIX_WITHDRAW_PIN


SITE = "https://www.5gbet.com/"
MODULE = SITE + "commonChunk.test.js"
HTML = "<html><script type='module' src='/commonChunk.test.js'></script></html>"
CPF = "52998224725"
KEY = "11987654321"


@pytest.mark.parametrize("scenario,expected_state,expected_stage", [
    ("success", "confirmed", "withdrawInfoV2-depois"),
    ("security-fail", "failed", "security/status"),
    ("modify-fail", "failed", "modifyWithdrawPass"),
    ("verify-fail", "failed", "verifyWithdrawPass"),
    ("authorization-fail", "failed", "verifyWithdrawalPasswordV2"),
])
def test_first_time_withdrawal_setup_stages_are_sequential(
        scenario, expected_state, expected_stage):
    sdk = r"""
        window.__calls=[];
        window.__bound=false;
        export const n={name:'lobby', setupCustomConfig(){}, Request:class Request {}, request:async ({url,method,data})=>{
          window.__calls.push({url,method,data});
          const reply=(body={},code=1)=>({data:{code,data:body}});
          const scenario="SCENARIO";
          if(url.endsWith('/member/user/security/status'))
            return reply({},scenario==='security-fail'?401:1);
          if(url.endsWith('/member/user/security/modifyWithdrawPass')){
            if(data.withdraw_pass!=="EXPECTED_PIN" || data.secondVerify?.type!==0)
              throw Error('mock expected initial PIN');
            return reply({},scenario==='modify-fail'?402:1);
          }
          if(url.endsWith('/member/user/security/verifyWithdrawPass')){
            if(data.withdraw_pass!=="EXPECTED_PIN")
              throw Error('mock expected verification PIN');
            return reply({},scenario==='verify-fail'?403:1);
          }
          if(url.endsWith('/finance/certify/withdrawInfoV2')){
            return reply({cpf:'52998224725',withdrawAccounts:
              window.__bound?[{account:'11987654321'}]:[]});
          }
          if(url.endsWith('/finance/certify/verifyWithdrawalPasswordV2')){
            if(data.withdrawalPassword!=="EXPECTED_PIN" ||
               data.addWithdrawAccountType!==5)
              throw Error('mock expected same PIN');
            return reply({key:'local-authorization',twoStepVerify:false},
              scenario==='authorization-fail'?404:1);
          }
          if(url.endsWith('/finance/certify/bindalipayV3')){
            if(data.withdrawalUUID!=='local-authorization' ||
               data.account!=='11987654321' || data.name!=='Teste Local')
              throw Error('mock binding mismatch');
            window.__bound=true;
            return reply({});
          }
          throw Error('unexpected local route');
        }};
    """.replace("SCENARIO",scenario).replace("EXPECTED_PIN",PIX_WITHDRAW_PIN)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            context=browser.new_context(ignore_https_errors=True)
            def intercept(route):
                if route.request.url==SITE:
                    route.fulfill(status=200,content_type="text/html",body=HTML)
                elif route.request.url==MODULE:
                    route.fulfill(status=200,content_type="text/javascript",body=sdk)
                else:
                    route.abort()
            context.route("**/*",intercept)
            page=context.new_page()
            page.goto(SITE,wait_until="domcontentloaded")
            result=bind_in_page(page,name="Teste Local",cpf=CPF,
                                pin=PIX_WITHDRAW_PIN,key=KEY,kind="PHONE",
                                initialize_pin=True)
            assert result["state"]==expected_state
            assert result["stage"]==expected_stage
            calls=page.evaluate("window.__calls.map(x=>x.url.split('/').pop())")
            assert calls[:1]==["status"]
            if expected_stage!="security/status":
                assert calls[1]=="modifyWithdrawPass"
            if expected_stage not in ("security/status","modifyWithdrawPass"):
                assert calls[2]=="verifyWithdrawPass"
            assert calls.count("bindalipayV3")== (1 if scenario=="success" else 0)
            stages=[item["stage"] for item in result["trace"]]
            assert expected_stage in stages
            serialized=json.dumps(result,ensure_ascii=False)
            for value in (PIX_WITHDRAW_PIN,CPF,KEY,"local-authorization"):
                assert value not in serialized
        finally:
            browser.close()
