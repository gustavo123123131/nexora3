"""Browser UI regression: local account status updates without app reload."""
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def test_live_status_refresh_tracks_each_account_without_reload():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content('<main id="mount"></main>')
            page.add_script_tag(path=str(ROOT / "webui/static/backup_view.js"))
            page.add_script_tag(path=str(ROOT / "webui/static/accounts_view.js"))
            page.evaluate("""() => {
              function make(id) {
                return {id, label:'Conta ' + id, accountId:'local-' + id,
                        username:'user' + id, email:'user' + id + '@example.test',
                        origin: id === 80 ? 'filha' : 'mae',
                        deviceMode:'android', hasRuntimeConfig:true,
                        platformUserId:null, status:'pending'};
              }
              window.accounts = [make(79), make(80), make(81)];
              window.view = NexoraAccountsView.create({
                get: async url => {
                  if (url === '/api/local-profiles') {
                    return {profiles: window.accounts.map(x => ({...x}))};
                  }
                  if (url === '/api/account-backups') return {backups:[]};
                  if (url.startsWith('/api/local-profiles/detail')) {
                    const id = Number(new URL(url,'http://localhost').searchParams.get('id'));
                    return {...window.accounts.find(x => x.id === id)};
                  }
                  throw Error('Unexpected GET');
                },
                post: async () => { throw Error('Unexpected mutation'); },
              });
              document.querySelector('#mount').append(view.node);
              view.onShow();
            }""")
            badges = page.locator(".accounts-current-panel .accounts-status-label")
            expect(badges).to_have_text(["Pendente", "Pendente", "Pendente"])
            # The waiting state arrives through the next background poll.
            page.evaluate("""() => {
              accounts[0].status = 'confirming';
              accounts[0].statusMessage = 'Confirmando cadastro pelo site.';
            }""")
            expect(badges).to_have_text(
                ["Confirmando cadastro", "Pendente", "Pendente"], timeout=6000
            )
            page.evaluate("""() => {
              accounts[0].status = 'created';
              accounts[0].statusMessage = 'Cadastro confirmado.';
              accounts[1].status = 'rejected';
              accounts[1].statusMessage = 'Cadastro recusado.';
              accounts[2].status = 'unconfirmed';
              accounts[2].statusMessage = 'Resultado final não comprovado.';
            }""")
            expect(badges).to_have_text(
                ["Criada", "Recusada", "Não confirmada"], timeout=6000
            )
            assert page.evaluate("window.accounts.every(x => x.platformUserId === null)")
            # Unchanged refreshes must not collapse open account details.
            page.locator(".accounts-current-panel .accounts-local-card").first.get_by_role(
                "button", name="Detalhes"
            ).click()
            expect(page.locator(".accounts-expanded").first).to_be_visible()
            page.wait_for_timeout(2100)
            expect(page.locator(".accounts-expanded").first).to_be_visible()
        finally:
            browser.close()
