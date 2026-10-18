"""Exercise the actual browser JS with a fake module/client; no real website calls."""
import json
import shutil
import subprocess

import pytest

from nexora_accounts.withdrawal_submit import _JS


@pytest.mark.skipif(not shutil.which('node'),reason='Node.js unavailable')
def test_one_real_submission_path_with_fake_sdk():
    js=_JS.replace('await import(url)','await loadModule(url)')
    harness=r'''(async () => {
      global.location={protocol:'https:',hostname:'www.5gbet.com',
        origin:'https://www.5gbet.com',href:'https://www.5gbet.com/account'};
      global.performance={getEntriesByType:()=>[{name:'https://www.5gbet.com/commonChunk.mock.js'}]};
      global.document={querySelectorAll:()=>[]};
      const called=[];
      global.loadModule=async()=>({n:{name:'lobby',setupCustomConfig(){},listenLoginStatus(){},
        async request(config){
          called.push({url:config.url,data:config.data});
          let data;
          if(config.url.endsWith('/withdrawInfoV2'))data={hasWithdrawPasswd:true,
            withdrawMin:5,withdrawMax:10000000,isEnableChannel:true,
            withdrawClose:{},accounts:[{id:'remote-1',accountType:7}]};
          else if(config.url.endsWith('/gameCenter/gold'))data={game_gold:26};
          else if(config.url.endsWith('/getWithdrawFee'))data={fee:0};
          else if(config.url.endsWith('/cashV3'))data={orderNo:'order-1',status:1};
          else if(config.url.endsWith('/orderInfo'))data={status:1};
          else throw Error('unexpected route '+config.url);
          return {status:200,data:{code:1,data}};
        }}});
      const fn=eval('('+process.argv[1]+')');
      const result=await fn({pin:'123456'});
      process.stdout.write(JSON.stringify({result,called}));
    })().catch(e=>{console.error(e);process.exit(1)})'''
    proc=subprocess.run(['node','-e',harness,js],capture_output=True,text=True,timeout=10)
    assert proc.returncode==0,proc.stderr
    data=json.loads(proc.stdout)
    assert data['result']=={'status':'pending','stage':'orderInfo',
                            'message':'Pedido em acompanhamento; pagamento não confirmado.',
                            'orderNo':'order-1','amount':26,'fee':0,'providerStatus':1}
    assert [r['url'] for r in data['called']]==[
        '/api/finance/certify/withdrawInfoV2','/api/gameCenter/gold',
        '/api/finance/certify/getWithdrawFee',
        '/api/finance/certify/cashV3','/api/finance/certify/orderInfo']
    cash=data['called'][3]
    assert cash['data']['id']=='remote-1'
    assert cash['data']['money']=='26'
    assert cash['data']['passwd']=='123456'
    assert cash['data']['accountType']==7
