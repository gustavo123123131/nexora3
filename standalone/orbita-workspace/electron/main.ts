import { app, BrowserWindow } from 'electron';
import { join } from 'node:path';
import { Repository } from './database';
import { ProfileWindows } from './profiles';
import { registerIpc } from './ipc';

const locked = app.requestSingleInstanceLock();
if (!locked) {
  app.quit();
} else {
  let main: BrowserWindow | null = null;
  let repo: Repository | null = null;
  let profiles: ProfileWindows | null = null;

  app.on('second-instance', () => {
    if (!main || main.isDestroyed()) return;
    if (main.isMinimized()) main.restore();
    main.focus();
  });

  async function createApplication() {
    repo = await Repository.open(join(app.getPath('userData'), 'orbita.sqlite'));
    profiles = new ProfileWindows(repo);

    main = new BrowserWindow({
      width: 1400, height: 900, minWidth: 860, minHeight: 620,
      backgroundColor: '#0e1420', show: false, title: 'Órbita Workspace',
      autoHideMenuBar: true,
      webPreferences: {
        preload: join(__dirname, 'preload.js'),
        contextIsolation: true, nodeIntegration: false,
        sandbox: true, webSecurity: true, webviewTag: false,
      },
    });
    registerIpc(repo, profiles, () => main?.webContents.id);
    main.once('ready-to-show', () => main?.show());
    main.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    main.webContents.on('will-navigate', (event) => event.preventDefault());

    const devServer = process.env.VITE_DEV_SERVER_URL;
    if (devServer && /^http:\/\/127\.0\.0\.1:5173\/?$/.test(devServer)) {
      await main.loadURL(devServer);
    } else {
      await main.loadFile(join(__dirname, '..', 'dist', 'index.html'));
    }
  }

  app.whenReady().then(createApplication).catch(error => {
    console.error('Falha na inicialização:', error instanceof Error ? error.message : 'erro desconhecido');
    app.quit();
  });
  app.on('before-quit', () => { profiles?.closeAll(); repo?.close(); });
  app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
  app.on('activate', () => {
    // The existing desktop window is brought back if present.
    if (main && !main.isDestroyed()) main.show();
  });
}
