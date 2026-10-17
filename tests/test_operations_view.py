"""Read-only Operations tab: UI and per-account identity binding regressions.

All requests are local fakes. No account opening or financial mutation occurs.
"""
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def open_view(page):
    page.set_content('<main id="mount"></main>')
    page.add_style_tag(path=str(ROOT / "webui/static/accounts_view.css"))
    page.add_style_tag(path=str(ROOT / "webui/static/operations_view.css"))
    page.add_script_tag(path=str(ROOT / "webui/static/operations_view.js"))
    page.evaluate("""() => {
      window.calls = [];
      window.accounts = [
        {id: 11, accountId: 'uuid-a', label: 'Conta 11', username: 'ana',
          platformUserId: '772104782', platformObservedUserId: null},
        {id: 22, accountId: 'uuid-b', label: 'Conta 22', username: 'bia',
          platformUserId: null, platformObservedUserId: '881111222'},
        {id: 33, accountId: 'uuid-c', label: 'Conta 33', username: 'sem',
          platformUserId: null, platformObservedUserId: null},
      ];
      window.fakeGet = async path => {
        window.calls.push(path);
        if (path === '/api/local-profiles') {
          return {profiles: window.accounts.map(p => ({...p}))};
        }
        if (path.startsWith('/api/local-profiles/detail?id=')) {
          const id = Number(new URL(path, 'http://localhost').searchParams.get('id'));
          const found = window.accounts.find(p => p.id === id);
          if (!found) throw Error('Conta não encontrada.');
          return {...found};
        }
        throw Error('Rota inesperada: ' + path);
      };
      window.ops = NexoraOperationsView.create({get: window.fakeGet});
      document.querySelector('#mount').append(window.ops.node);
      window.ops.onShow();
    }""")


def test_operations_selects_correct_internal_account_and_labels_observed_uid():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1000, "height": 780})
            open_view(page)
            expect(page.get_by_role("heading", name="Operações", exact=True)).to_be_visible()
            expect(page.locator(".operations-card")).to_have_count(4)
            expect(page.get_by_text("Não disponível")).to_have_count(3)
            chooser = page.get_by_role("combobox", name="Selecionar conta para operações")
            expect(chooser.locator("option")).to_have_count(4)
            assert page.evaluate("window.ops.getSelectedAccount()") is None

            chooser.select_option("11")
            expect(page.locator(".operations-account-details")).to_contain_text("772104782")
            assert page.evaluate("window.ops.getSelectedAccount()") == {
                "profileId": 11, "accountId": "uuid-a",
                "platformId": "772104782", "identityStatus": "verified",
            }
            chooser.select_option("22")
            expect(page.locator(".operations-account-details")).to_contain_text("881111222")
            expect(page.locator(".operations-account-details")).to_contain_text(
                "Observado, não verificado"
            )
            assert page.evaluate("window.ops.getSelectedAccount()") == {
                "profileId": 22, "accountId": "uuid-b",
                "platformId": "881111222", "identityStatus": "observed",
            }
            chooser.select_option("33")
            expect(page.locator(".operations-feedback")).to_contain_text("não possui um ID")
            assert page.evaluate("window.ops.getSelectedAccount()") == {
                "profileId": 33, "accountId": "uuid-c",
                "platformId": None, "identityStatus": "unidentified",
            }
            chooser.select_option("")
            assert page.evaluate("window.ops.getSelectedAccount()") is None

            assert all(path == "/api/local-profiles" or
                path.startswith("/api/local-profiles/detail?id=")
                for path in page.evaluate("window.calls"))
            assert page.locator(".operations-card button").count() == 0
            assert page.locator(".operations-local form").count() == 0
        finally:
            browser.close()


def test_operations_selection_expires_on_deletion_or_internal_uuid_change():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            open_view(page)
            selector = page.get_by_role("combobox", name="Selecionar conta para operações")
            selector.select_option("11")
            expect(page.locator(".operations-account-details")).to_contain_text("772104782")
            page.evaluate("""() => {
              // Reusing a numeric profile id MUST NOT reuse an old remote uid.
              window.accounts[0] = {id: 11, accountId: 'uuid-new',
                label: 'Outra conta', username: 'nova',
                platformUserId: '990000001'};
            }""")
            page.get_by_role("button", name="Atualizar contas").click()
            expect(page.locator(".operations-feedback")).to_contain_text(
                "não está mais disponível"
            )
            assert page.evaluate("window.ops.getSelectedAccount()") is None
            assert selector.input_value() == ""
            selector.select_option("11")
            expect(page.locator(".operations-account-details")).to_contain_text("990000001")
            assert page.evaluate("window.ops.getSelectedAccount().accountId") == "uuid-new"
            page.evaluate("window.accounts = window.accounts.filter(p => p.id !== 11)")
            page.get_by_role("button", name="Atualizar contas").click()
            expect(selector.locator("option")).to_have_count(3)
            assert page.evaluate("window.ops.getSelectedAccount()") is None
        finally:
            browser.close()


def test_operations_rejects_divergent_detail_and_stale_async_response():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            open_view(page)
            selector = page.get_by_role("combobox", name="Selecionar conta para operações")

            # The list UUID must match the detail UUID, even if numeric
            # internal ids and remote platform ids both look reasonable.
            page.evaluate("""() => {
              const original = window.fakeGet;
              window.divergent = true;
              window.ops = null;
              // Replace API binding via a fresh view; no mutation is sent.
              const get = async path => {
                const result = await original(path);
                if (path.includes('detail?id=11') && window.divergent) {
                  return {...result, accountId: 'a-different-account'};
                }
                return result;
              };
              const view = NexoraOperationsView.create({get});
              document.querySelector('#mount').replaceChildren(view.node);
              window.ops = view;
              window.ops.onShow();
            }""")
            selector = page.get_by_role("combobox", name="Selecionar conta para operações")
            selector.select_option("11")
            expect(page.locator(".operations-feedback")).to_contain_text("divergentes")
            assert page.evaluate("window.ops.getSelectedAccount()") is None

            # Force a stale response from account 11 to arrive after account 22.
            page.evaluate("""() => {
              const get = async path => {
                if (path === '/api/local-profiles') return {profiles: window.accounts};
                if (path.includes('detail?id=11')) {
                  return new Promise(resolve => { window.releaseOld = () =>
                    resolve({...window.accounts[0]}); });
                }
                return {...window.accounts[1]};
              };
              const view = NexoraOperationsView.create({get});
              document.querySelector('#mount').replaceChildren(view.node);
              window.ops = view;
              window.ops.onShow();
            }""")
            selector = page.get_by_role("combobox", name="Selecionar conta para operações")
            selector.select_option("11")
            selector.select_option("22")
            expect(page.locator(".operations-account-details")).to_contain_text("881111222")
            page.evaluate("window.releaseOld()")
            page.wait_for_timeout(100)
            assert page.evaluate("window.ops.getSelectedAccount().accountId") == "uuid-b"
            assert "772104782" not in page.locator(".operations-account-details").inner_text()
        finally:
            browser.close()


def test_pix_prepare_ui_four_types_uses_local_routes_and_keeps_history():
    """An in-browser mock checks the UI contract; it never invokes finance APIs."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content('<main id="mount"></main>')
            page.add_style_tag(path=str(ROOT / "webui/static/accounts_view.css"))
            page.add_style_tag(path=str(ROOT / "webui/static/operations_view.css"))
            page.add_script_tag(path=str(ROOT / "webui/static/operations_view.js"))
            page.evaluate("""() => {
              window.calls = [];
              const keys = [
                {id:'a',type:'PHONE',value:'11987654321'},
                {id:'b',type:'EMAIL',value:'private@example.com'},
                {id:'c',type:'CPF',value:'52998224725'},
                {id:'d',type:'EVP',value:'123e4567-e89b-12d3-a456-426655440000'}
              ];
              let history = [];
              const get = async url => {
                window.calls.push('GET ' + url);
                if (url === '/api/local-profiles') return {profiles:[{
                  id:11, label:'Conta 11',username:'test',
                  accountId:'uuid-a',platformObservedUserId:'772104782',
                  platformUserId:null}]};
                if (url === '/api/local-profiles/detail?id=11') return {
                  id:11, label:'Conta 11', accountId:'uuid-a',
                  platformObservedUserId:'772104782',platformUserId:null};
                if (url === '/api/pix-keys') return {keys};
                if (url.startsWith('/api/operations/pix/history?')) return {operations:history};
                throw Error('Unexpected GET');
              };
              const post = async (url,body) => {
                window.calls.push('POST ' + url);
                if(url !== '/api/operations/pix/bind') throw Error('Unexpected POST');
                const key = keys.find(k=>k.id===body.keyId);
                if(!key||key.type!==body.keyType||body.accountId!=='uuid-a') throw Error('mismatch');
                const operation = {
                  operationId:'test-operation-'+history.length, keyType:key.type,
                  status:'confirmed', createdAt:'2026-10-17T00:00Z',
                  message:'Cadastro confirmado na lista de saques da conta.'
                };
                history=[operation,...history];
                return {operation, reused:false};
              };
              window.ops=NexoraOperationsView.create({get,post});
              document.querySelector('#mount').append(window.ops.node);
              window.ops.onShow();
            }""")
            selector = page.get_by_role("combobox", name="Selecionar conta para operações")
            selector.select_option("11")
            type_selector = page.get_by_role("combobox", name="Escolher tipo de chave Pix")
            key_selector = page.get_by_role("combobox", name="Escolher chave Pix cadastrada")
            prepare = page.get_by_role("button", name="Cadastrar")
            for typ, key in [("PHONE","a"),("EMAIL","b"),("CPF","c"),("EVP","d")]:
                type_selector.select_option(typ)
                expect(key_selector).to_have_value(key)
                expect(prepare).to_be_enabled()
                prepare.click()
                expect(page.locator(".operations-pix-feedback")).to_contain_text(
                    "Cadastro Pix confirmado na listagem"
                )
                expect(page.locator(".operations-history-item")).to_have_count(
                    ["PHONE","EMAIL","CPF","EVP"].index(typ)+1
                )
            for secret in ("11987654321","private@example.com",
                           "52998224725","123e4567-e89b-12d3-a456-426655440000"):
                assert secret not in page.locator(".operations-pix-panel").inner_text()
            paths = page.evaluate("window.calls")
            assert len([p for p in paths if p.startswith("POST ")]) == 4
            assert all('/api/operations/pix/bind' in p
                       for p in paths if p.startswith("POST "))
        finally:
            browser.close()


def test_direct_pix_preparation_uses_account_details_without_rechecking_id():
    """One click uses existing detail/UUID and key; no identity/API probe."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content('<main id="mount"></main>')
            page.add_script_tag(path=str(ROOT / "webui/static/operations_view.js"))
            page.evaluate("""() => {
                window.posts = [];
                const account = {id:52, accountId:'acct-local-52',label:'Conta 52',
                    username:'usuario52', fullName:'Marina Souza',
                    cpf:'52998224725', hasLoginPassword:true, hasWithdrawPin:true,
                    platformObservedUserId:'745550222',platformUserId:null};
                const get = async path => {
                    if (path === '/api/local-profiles') return {profiles:[account]};
                    if (path.includes('/api/local-profiles/detail?id='))
                        return {...account};
                    if (path === '/api/pix-keys') return {keys:[
                        {id:'key-52',type:'PHONE',value:'11987654321'}
                    ]};
                    if (path.includes('/api/operations/pix/history')) return {operations:[]};
                    throw Error('Unexpected GET '+path);
                };
                const post = async (path, data) => {
                    window.posts.push({path, data});
                    if (path !== '/api/operations/pix/bind')
                        throw Error('Unexpected POST');
                    return {reused:false, operation:{operationId:'test-op',status:'confirmed'}};
                };
                const view=NexoraOperationsView.create({get,post});
                document.querySelector('#mount').append(view.node);
                view.onShow();
            }""")
            page.get_by_role("combobox", name="Selecionar conta para operações").select_option("52")
            detail = page.locator(".operations-account-details")
            expect(detail).to_contain_text("Marina Souza")
            expect(detail).to_contain_text("usuario52")
            expect(detail).to_contain_text("Padrão do sistema (387520)")
            assert "52998224725" not in detail.inner_text()
            assert "Não informado" not in detail.inner_text()
            for obsolete in ("Testar API na conta aberta", "Verificar ID da plataforma",
                             "Verificar requisitos da API", "Confirmar autorização da chave Pix"):
                assert page.get_by_text(obsolete, exact=True).count() == 0
            page.get_by_role("combobox", name="Escolher tipo de chave Pix").select_option("PHONE")
            prepare=page.get_by_role("button", name="Cadastrar")
            expect(prepare).to_be_enabled()
            prepare.click()
            expect(page.locator(".operations-pix-feedback")).to_contain_text(
                "Cadastro Pix confirmado na listagem")
            entries=page.evaluate("window.posts")
            assert len(entries)==1
            assert entries[0]["path"]=="/api/operations/pix/bind"
            assert entries[0]["data"]=={
                "profileId":52,"accountId":"acct-local-52",
                "keyId":"key-52","keyType":"PHONE"
            }
        finally:
            browser.close()
