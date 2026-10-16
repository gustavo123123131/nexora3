"""Offline Chromium checks for instant account-card deletion and nested backups."""
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def test_one_click_delete_hides_card_and_backup_is_nested():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content('<main id="mount"></main>')
            page.add_style_tag(path=str(ROOT / "webui/static/accounts_view.css"))
            page.add_script_tag(path=str(ROOT / "webui/static/backup_view.js"))
            page.add_script_tag(path=str(ROOT / "webui/static/accounts_view.js"))
            page.evaluate("""() => {
              window.confirmCalls = 0;
              window.confirm = () => { window.confirmCalls++; return false; };
              const make = (id) => ({
                id, label: 'Conta ' + id, username: 'user' + id,
                email: 'user' + id + '@example.test',
                origin: 'mae', deviceMode: 'android', hasRuntimeConfig: true,
                status: 'pending'
              });
              window.profiles = [make(1), make(2)];
              window.backups = [];
              const api = {
                get: async (url) => {
                  if (url === '/api/local-profiles')
                    return {profiles: [...window.profiles]};
                  if (url === '/api/account-backups')
                    return {backups: [...window.backups]};
                  throw Error('Unknown GET ' + url);
                },
                post: async (url, data) => {
                  if (url === '/api/local-profiles/delete') {
                    return new Promise((resolve, reject) => {
                      window.finishDelete = () => {
                        window.profiles = window.profiles.filter(p => p.id !== data.id);
                        window.backups.push({
                          id: 'backup-1', profileId: data.id, accountId: 'id-1',
                          label: 'Conta ' + data.id, restored: false,
                          device: 'Android', fileCount: 1, hasBrowserData: true
                        });
                        resolve({ok:true, profileId:data.id, backupId:'backup-1'});
                      };
                      window.rejectDelete = () => reject(Error('Feche a sessão desta conta antes de excluir.'));
                    });
                  }
                  if (url === '/api/account-backups/restore') {
                    window.profiles.push(make(1));
                    window.backups[0].restored = true;
                    return {ok: true, profileId: 1};
                  }
                  throw Error('Unknown POST ' + url);
                }
              };
              const view = NexoraAccountsView.create(api);
              document.querySelector('#mount').append(view.node);
              view.onShow();
            }""")
            cards = page.locator(".accounts-current-panel .accounts-local-card")
            expect(cards).to_have_count(2)
            page.locator(".accounts-current-panel .accounts-local-card").first.get_by_role(
                "button", name="Excluir"
            ).click()
            expect(cards).to_have_count(1)
            assert page.evaluate("window.confirmCalls") == 0
            assert page.get_by_text("exclusão em andamento", exact=False).is_visible()
            page.evaluate("window.finishDelete()")
            expect(cards).to_have_count(1)
            expect(page.get_by_text("com backup em Contas > Backup.", exact=False)).to_be_visible()

            page.locator(".accounts-inner-tabs").get_by_role("button", name="Backup").click()
            expect(page.locator(".accounts-backup-panel .accounts-local-card")).to_have_count(1)
            expect(page.locator(".accounts-backup-panel")).to_be_visible()
            expect(page.locator(".accounts-current-panel")).to_be_hidden()
            # Restoring is still a separate explicit action.
            page.evaluate("window.confirm = () => true")
            page.locator(".accounts-backup-panel").get_by_role("button", name="Restaurar").click()
            expect(page.locator(".accounts-backup-panel .accounts-status-label")).to_have_text("Restaurada")
            page.locator(".accounts-inner-tabs").get_by_role("button", name="Contas").click()
            expect(cards).to_have_count(2)

            # A rejected deletion must restore the hidden card automatically.
            page.locator(".accounts-current-panel .accounts-local-card").last.get_by_role(
                "button", name="Excluir"
            ).click()
            expect(cards).to_have_count(1)
            page.evaluate("window.rejectDelete()")
            expect(cards).to_have_count(2)
            expect(page.get_by_text("Feche a sessão desta conta", exact=False)).to_be_visible()
        finally:
            browser.close()


def test_account_detail_shows_remote_id_separate_from_local_uuid():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content('<main id="mount"></main>')
            page.add_script_tag(path=str(ROOT / "webui/static/backup_view.js"))
            page.add_script_tag(path=str(ROOT / "webui/static/accounts_view.js"))
            page.evaluate("""() => {
              const list = [
                {id:79, label:'Conta 79', accountId:'uuid-interno-79',
                 username:'first', email:'first@example.test', origin:'mae',
                 status:'created', deviceMode:'android',
                 registrationReviewRequired:true, platformUserId:null},
                {id:80, label:'Conta 80', accountId:'uuid-interno-80',
                 username:'second', email:'second@example.test', origin:'filha',
                 status:'created', deviceMode:'android',
                 registrationReviewRequired:false, platformUserId:'000042',
                 registrationConfirmationSource:'synthetic-test-source'}
              ];
              const api = {
                get: async (url) => {
                  if (url === '/api/local-profiles') return {profiles:list};
                  if (url === '/api/account-backups') return {backups:[]};
                  if (url.startsWith('/api/local-profiles/detail')) {
                    const id = Number(new URL(url,'http://localhost').searchParams.get('id'));
                    return list.find(item => item.id === id);
                  }
                  throw Error('Unknown endpoint');
                },
                post: async () => { throw Error('Unexpected mutation'); }
              };
              const view = NexoraAccountsView.create(api);
              document.querySelector('#mount').append(view.node);
              view.onShow();
            }""")
            cards = page.locator(".accounts-current-panel .accounts-local-card")
            expect(cards).to_have_count(2)
            cards.nth(0).get_by_role("button", name="Detalhes").click()
            expect(cards.nth(0).locator(".accounts-expanded")).to_contain_text(
                "Confirmação antiga — revisão necessária")
            expect(cards.nth(0).locator(".accounts-expanded")).to_contain_text(
                "Não identificado")
            expect(cards.nth(0).locator(".accounts-expanded")).to_contain_text(
                "uuid-interno-79")
            cards.nth(1).get_by_role("button", name="Detalhes").click()
            expect(cards.nth(1).locator(".accounts-expanded")).to_contain_text(
                "ID da plataforma")
            expect(cards.nth(1).locator(".accounts-expanded")).to_contain_text(
                "000042")
            expect(cards.nth(1).locator(".accounts-expanded")).to_contain_text(
                "synthetic-test-source")
            expect(cards.nth(1).locator(".accounts-expanded")).to_contain_text(
                "uuid-interno-80")
        finally:
            browser.close()
