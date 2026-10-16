"""Offline Chromium interaction checks for the new Pix tab."""
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def test_pix_view_add_edit_delete_validation_and_proxy_style():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1100, "height": 750})
            page.set_content('<main id="mount"></main>')
            page.add_style_tag(path=str(ROOT / "webui/static/proxy_view.css"))
            page.add_style_tag(path=str(ROOT / "webui/static/pix_view.css"))
            page.add_script_tag(path=str(ROOT / "webui/static/pix_view.js"))
            page.evaluate("""() => {
              window.fakeRecords = [];
              window.calls = [];
              window.pixApi = {
                get: async (url) => {
                  if (url !== '/api/pix-keys') throw Error('unexpected route');
                  return {keys: window.fakeRecords.slice()};
                },
                post: async (url, body) => {
                  window.calls.push(url);
                  if (url === '/api/pix-keys/add') {
                    const key = {id: 'record-1', type: body.type,
                      value: body.value.toLowerCase(), createdAt: 't0', updatedAt: 't0'};
                    window.fakeRecords.push(key);
                    return {key};
                  }
                  if (url === '/api/pix-keys/update') {
                    const index = window.fakeRecords.findIndex(k => k.id === body.id);
                    const key = {...window.fakeRecords[index], type: body.type,
                      value: body.value.toLowerCase(), updatedAt: 't1'};
                    window.fakeRecords[index] = key;
                    return {key};
                  }
                  if (url === '/api/pix-keys/delete') {
                    window.fakeRecords = window.fakeRecords.filter(k => k.id !== body.id);
                    return {ok: true, id: body.id};
                  }
                  throw Error('unexpected route');
                }
              };
              window.pixView = NexoraPixView.create(window.pixApi);
              document.querySelector('#mount').append(window.pixView.node);
              window.pixView.onShow();
            }""")
            assert page.get_by_role("heading", name="Gerenciamento de Chaves Pix").is_visible()
            assert page.locator(".pix-simple").evaluate(
                "el => getComputedStyle(el).maxWidth"
            ) == "850px"
            page.get_by_role("button", name="Adicionar Chave Pix").click()
            field = page.get_by_role("textbox", name="Chave Pix")
            field.fill("isto não é chave")
            assert page.get_by_text("Formato inválido.").is_visible()
            page.get_by_role("button", name="Salvar", exact=True).click()
            assert page.evaluate("window.calls.length") == 0
            field.fill("Contato@Example.com")
            assert page.get_by_text("Tipo identificado: E-mail").is_visible()
            page.get_by_role("button", name="Salvar", exact=True).click()
            assert page.get_by_text("contato@example.com", exact=True).is_visible()
            page.get_by_role("button", name="Editar").click()
            assert field.input_value() == "contato@example.com"
            field.fill("user@EXAMPLE.org")
            page.get_by_role("button", name="Salvar alteração").click()
            assert page.get_by_text("user@example.org", exact=True).is_visible()
            assert page.get_by_text("contato@example.com", exact=True).count() == 0
            page.locator(".pix-remove").click()
            assert page.get_by_text("Nenhuma chave Pix cadastrada.").is_visible()
            assert page.evaluate("window.calls") == [
                "/api/pix-keys/add", "/api/pix-keys/update", "/api/pix-keys/delete"
            ]
        finally:
            browser.close()


def test_pix_client_flags_ambiguous_numeric_key():
    # Using the same shape with mathematically valid CPF digits.
    seed = "119123456"
    for length in (9, 10):
        total = sum(int(seed[i]) * (length + 1 - i) for i in range(length))
        check = total * 10 % 11
        seed += str(0 if check == 10 else check)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content('<main id="root"></main>')
            page.add_script_tag(path=str(ROOT / "webui/static/pix_view.js"))
            page.evaluate("""() => {
              window.actions = [];
              let records = [];
              const api = {
                get: async () => ({keys: records}),
                post: async (path, body) => {
                  window.actions.push(body);
                  return {key: {id:'id1', value:body.value, type:body.type}};
                }
              };
              document.querySelector('#root').append(
                NexoraPixView.create(api).node
              );
            }""")
            page.get_by_role("button", name="Adicionar Chave Pix").click()
            page.get_by_role("textbox", name="Chave Pix").fill(seed)
            assert page.get_by_text("A chave pode ser CPF ou Telefone. Escolha o tipo:").is_visible()
            page.get_by_role("button", name="Salvar", exact=True).click()
            assert page.evaluate("window.actions.length") == 0
            page.get_by_role("combobox", name="Escolher tipo da chave Pix").select_option("CPF")
            page.get_by_role("button", name="Salvar", exact=True).click()
            assert page.evaluate("window.actions[0].type") == "CPF"
        finally:
            browser.close()


def test_telephone_key_renders_without_plus_55_and_can_be_edited():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content('<main id="root"></main>')
            page.add_script_tag(path=str(ROOT / "webui/static/pix_view.js"))
            page.evaluate("""() => {
              let records = [];
              window.sentPhone = '';
              const api = {
                get: async () => ({keys: [...records]}),
                post: async (path, body) => {
                  window.sentPhone = body.value;
                  const value = body.type === 'PHONE'
                    ? body.value.replace(/^\\+55/, '').replace(/\\D/g, '')
                    : body.value;
                  const record = {
                    id: path.endsWith('/update') ? body.id : 'phone-1',
                    type: body.type, value, createdAt: 'before', updatedAt: 'now'
                  };
                  records = [record];
                  return {key: record};
                }
              };
              const view = NexoraPixView.create(api);
              document.querySelector('#root').append(view.node);
              view.onShow();
            }""")
            page.get_by_role("button", name="Adicionar Chave Pix").click()
            field = page.get_by_role("textbox", name="Chave Pix")
            field.fill("+55 (11) 98765-4321")
            assert page.get_by_text("Tipo identificado: Telefone").is_visible()
            page.get_by_role("button", name="Salvar", exact=True).click()
            assert page.locator(".pix-simple-info code").inner_text() == "11987654321"
            page.get_by_role("button", name="Editar").click()
            assert field.input_value() == "11987654321"
            field.fill("(11) 91234-5678")
            page.get_by_role("button", name="Salvar alteração").click()
            assert page.locator(".pix-simple-info code").inner_text() == "11912345678"
        finally:
            browser.close()


def test_legacy_phone_display_is_national_during_agent_update():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content('<main id="root"></main>')
            page.add_script_tag(path=str(ROOT / "webui/static/pix_view.js"))
            page.evaluate("""() => {
              const api = {
                get: async () => ({keys: [{
                  id: 'legacy', type: 'PHONE', value: '+5511987654321',
                  createdAt: 'before', updatedAt: 'before'
                }]}),
                post: async () => { throw Error('Unexpected mutation'); }
              };
              const view = NexoraPixView.create(api);
              document.querySelector('#root').append(view.node);
              view.onShow();
            }""")
            assert page.locator(".pix-simple-info code").inner_text() == "11987654321"
            page.get_by_role("button", name="Editar").click()
            assert page.get_by_role("textbox", name="Chave Pix").input_value() == "11987654321"
        finally:
            browser.close()
