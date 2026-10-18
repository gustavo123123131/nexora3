"""Mocked account-bound withdrawal flow; never submits external transactions."""
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from nexora_accounts.browser_sessions import claim_account, mark_browser_ready
import nexora_accounts.session_launcher as launcher
import nexora_accounts.withdrawal_submit as submit


def profiles():
    return {n:{"id":n,"accountId":str(uuid4()),"origin":kind,"lastSessionId":str(uuid4())}
            for n,kind in ((71001,"mae"),(71002,"filha"))}


def test_store_isolated_and_no_second_send(tmp_path):
    people=profiles()
    reader=SimpleNamespace(public_detail=lambda i:dict(people[i]))
    store=submit.WithdrawalSubmissionStore(tmp_path/'orders.json',profiles=reader)
    a,b=people.values()
    one=store.reserve(profile_id=a['id'],account_id=a['accountId'],run_id=a['lastSessionId'])
    op=one['operation']
    again=store.reserve(profile_id=a['id'],account_id=a['accountId'],run_id=a['lastSessionId'])
    assert again['reused'] is True
    assert again['operation']['operationId']==op['operationId']
    store.update(op['operationId'],profile_id=a['id'],account_id=a['accountId'],
                 run_id=a['lastSessionId'],status='attempting',stage='cashV3',
                 message='Tentativa marcada antes do envio; não repetir automaticamente.')
    held=store.reserve(profile_id=a['id'],account_id=a['accountId'],run_id=a['lastSessionId'])
    assert held['reused'] is True
    assert held['operation']['submissionSent'] is True
    assert not store.list(b['id'])['orders']
    other=store.reserve(profile_id=b['id'],account_id=b['accountId'],run_id=b['lastSessionId'])
    assert other['reused'] is False
    store.update(op['operationId'],profile_id=a['id'],account_id=a['accountId'],
                 run_id=a['lastSessionId'],status='pending',stage='orderInfo',
                 message='Pedido em acompanhamento; pagamento não confirmado.',
                 orderNo='order-987',amount=26,fee=0,providerStatus=1)
    saved=store.list(a['id'])['orders'][0]
    assert saved['orderNo']=='order-987'
    assert saved['providerStatus']==1
    assert saved['status']=='pending' and saved['submissionSent'] is True
    assert store.reserve(profile_id=a['id'],account_id=a['accountId'],run_id=a['lastSessionId'])['reused']
    with pytest.raises(submit.WithdrawalSubmissionError):
        store.get(op['operationId'],profile_id=b['id'],account_id=b['accountId'],
                  run_id=b['lastSessionId'])


def test_preflight_failure_from_own_page_does_not_block_future_requests(tmp_path):
    account=next(iter(profiles().values()))
    reader=SimpleNamespace(public_detail=lambda _:dict(account))
    store=submit.WithdrawalSubmissionStore(tmp_path/'orders.json',profiles=reader)
    op=store.reserve(profile_id=account['id'],account_id=account['accountId'],
                      run_id=account['lastSessionId'])['operation']
    where=dict(op_id=op['operationId'],profile_id=account['id'],
               account_id=account['accountId'],run_id=account['lastSessionId'])
    store.update(**where,status='attempting',stage='cashV3',
                 message='Tentativa marcada antes do envio; não repetir automaticamente.')
    blocked=store.update(**where,status='blocked',stage='withdrawInfoV2',
                         message='Envio não iniciado: destino único não confirmado.')
    assert blocked['status']=='blocked' and not blocked['submissionSent']
    assert not store.reserve(profile_id=account['id'],account_id=account['accountId'],
                             run_id=account['lastSessionId'])['reused']


def test_exception_after_dispatch_is_indeterminate(tmp_path):
    account=next(iter(profiles().values()))
    reader=SimpleNamespace(public_detail=lambda _:dict(account))
    store=submit.WithdrawalSubmissionStore(tmp_path/'orders.json',profiles=reader)
    op=store.reserve(profile_id=account['id'],account_id=account['accountId'],
                      run_id=account['lastSessionId'])['operation']
    where=dict(op_id=op['operationId'],profile_id=account['id'],
               account_id=account['accountId'],run_id=account['lastSessionId'])
    store.update(**where,status='attempting',stage='cashV3',
                 message='Tentativa marcada antes do envio; não repetir automaticamente.')
    outcome=store.update(**where,status='blocked',stage='cashV3',
                         message='Envio não iniciado: consulta remota rejeitada.')
    assert outcome['status']=='indeterminate'
    assert outcome['submissionSent'] is True
    assert store.reserve(profile_id=account['id'],account_id=account['accountId'],
                         run_id=account['lastSessionId'])['reused']


def test_browser_evaluation_filters_secrets_and_requires_exact_host():
    class FakePage:
        url='https://www.5gbet.com/game'
        def evaluate(self,src,params):
            assert params=={'pin':'123456'}
            assert 'cashV3' in src and 'orderInfo' in src
            return {'status':'pending','stage':'orderInfo',
                    'message':'Pedido em acompanhamento; pagamento não confirmado.',
                    'orderNo':'real-order-1','amount':26,'fee':0,'providerStatus':1,
                    'token':'MUST_NOT_RETURN','pix':'NOT_RETURNED'}
    result=submit.send_in_page(FakePage(),'123456')
    assert result=={'status':'pending','stage':'orderInfo',
                    'message':'Pedido em acompanhamento; pagamento não confirmado.',
                    'orderNo':'real-order-1','amount':26,'fee':0,'providerStatus':1}
    p=FakePage();p.url='https://not5gbet.com/'
    assert submit.send_in_page(p,'123456')['status']=='blocked'


def test_submit_uses_exact_account_worker_and_stored_pin(monkeypatch,tmp_path):
    people=profiles();a,b=people.values();called=[]
    class Reader:
        def public_detail(self,pid):return dict(people[pid])
        def read_withdraw_pin(self,pid):
            called.append(('pin-read',pid));return '123456'
    reader=Reader()
    monkeypatch.setattr(launcher,'LocalProfileStore',lambda:reader)
    store=submit.WithdrawalSubmissionStore(tmp_path/'orders.json',profiles=reader)
    monkeypatch.setattr(submit,'WithdrawalSubmissionStore',lambda:store)
    def fake_send(page,pin):
        called.append(('send',page,pin))
        return {'status':'pending','stage':'orderInfo',
                'message':'Pedido em acompanhamento; pagamento não confirmado.',
                'orderNo':'test-1001','amount':26,'fee':0,'providerStatus':1}
    monkeypatch.setattr(submit,'send_in_page',fake_send)
    app=launcher.AccountLauncher()
    with claim_account(a['origin'],a['id'],account_uuid=a['accountId'],
                       stop_event=threading.Event(),run_id=a['lastSessionId']) as worker_a:
        with claim_account(b['origin'],b['id'],account_uuid=b['accountId'],
                           stop_event=threading.Event(),run_id=b['lastSessionId']) as worker_b:
            mark_browser_ready(worker_a);mark_browser_ready(worker_b)
            responses=[]
            t=threading.Thread(target=lambda: responses.append(
                app.submit_withdrawal(a['id'],a['accountId'],timeout=2)))
            t.start()
            for _ in range(200):
                if not worker_a['requests'].empty():break
                time.sleep(.005)
            assert not worker_a['requests'].empty()
            assert worker_b['requests'].empty()
            pa=object()
            app._process_browser_request(a['id'],a['accountId'],worker_a['requests'],
                                         pa,run_id=a['lastSessionId'])
            t.join(timeout=3)
            assert not t.is_alive()
            assert responses[0]['operation']['status']=='pending'
            assert ('send',pa,'123456') in called
            assert all(call[1]!=b['id'] for call in called if call[0]=='pin-read')
            again=app.submit_withdrawal(a['id'],a['accountId'],timeout=.1)
            assert again['reused'] is True
            assert worker_a['requests'].empty()
            assert worker_b['requests'].empty()
            with pytest.raises(submit.WithdrawalSubmissionError):
                app.submit_withdrawal(a['id'],b['accountId'],timeout=.1)
