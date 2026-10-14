/* End-to-end local Electron smoke test; no external URL is visited.
 * Requires npm run build and xvfb-run on headless Linux.
 */
const { _electron: electron } = require('playwright');
const assert = require('node:assert/strict');
const { resolve, join } = require('node:path');
const { mkdirSync } = require('node:fs');

async function run() {
  const root = resolve(__dirname, '..');
  const artifacts = join(root, 'ui-screenshots');
  mkdirSync(artifacts, { recursive: true });
  const application = await electron.launch({
    args: ['--no-sandbox', root],
    cwd: root,
    timeout: 60000,
    env: { ...process.env, ELECTRON_DISABLE_SECURITY_WARNINGS: 'true' },
  });
  try {
    const page = await application.firstWindow();
    await page.getByRole('heading', { name: 'Visão geral' }).waitFor({ timeout: 30000 });
    await page.screenshot({ path: join(artifacts, 'orbita-dashboard.png'), fullPage: true });
    await page.locator('.nav-link').filter({ hasText: 'Contas' }).click();
    await page.getByRole('button', { name: /Nova conta/ }).click();
    await page.locator('input[placeholder="Ex.: Conta principal"]').fill('Conta de teste local');
    await page.locator('input[placeholder="Nome de referência"]').fill('Provedor de teste');
    await page.getByRole('button', { name: 'Salvar conta' }).click();
    await page.getByText('Conta de teste local', { exact: true }).waitFor({ timeout: 12000 });
    await page.screenshot({ path: join(artifacts, 'orbita-contas.png'), fullPage: true });

    await page.locator('.nav-link').filter({ hasText: 'Perfis' }).click();
    for (const label of ['Perfil isolado A', 'Perfil isolado B']) {
      await page.getByRole('button', { name: /Novo perfil/ }).click();
      await page.locator('input[placeholder="Ex.: Meu perfil 01"]').fill(label);
      await page.getByRole('button', { name: 'Salvar perfil' }).click();
      await page.getByRole('heading', { name: label }).waitFor({ timeout: 12000 });
    }
    assert.equal(await page.locator('.profile-card').count(), 2);
    await page.screenshot({ path: join(artifacts, 'orbita-perfis.png'), fullPage: true });

    for (let i = 0; i < 2; i++) {
      await page.locator('.profile-card').nth(i).getByRole('button', { name: /Abrir navegador/ }).click();
      await page.waitForTimeout(450);
    }
    const isolated = await application.evaluate(async ({ BrowserWindow }) => {
      const pages = BrowserWindow.getAllWindows().filter(win => win.webContents.getURL() === 'about:blank');
      if (pages.length !== 2) return { opened: pages.length, first: -1, second: -1 };
      await pages[0].webContents.session.cookies.set({
        url: 'https://example.com', name: 'orbita_isolation_probe', value: 'test-only',
      });
      const first = await pages[0].webContents.session.cookies.get({ name: 'orbita_isolation_probe' });
      const second = await pages[1].webContents.session.cookies.get({ name: 'orbita_isolation_probe' });
      return { opened: pages.length, first: first.length, second: second.length };
    });
    assert.deepEqual(isolated, { opened: 2, first: 1, second: 0 },
      'Cookies não podem vazar entre partições de perfis distintos');

    await page.locator('.nav-link').filter({ hasText: 'Auditoria' }).click();
    await page.getByText('profile.open', { exact: true }).first().waitFor({ timeout: 12000 });
    await page.screenshot({ path: join(artifacts, 'orbita-auditoria.png'), fullPage: true });
    console.log('Electron GUI: conta local, perfis, isolamento de cookies e auditoria aprovados.');
  } finally {
    await application.close();
  }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
