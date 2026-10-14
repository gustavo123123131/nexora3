import { BrowserWindow, session } from 'electron';
import type { Repository } from './database';
import { id } from './validators';

export class ProfileWindows {
  private windows = new Map<string, BrowserWindow>();
  constructor(private readonly repo: Repository) {}

  async open(value: unknown): Promise<void> {
    const key = id(value), profile = this.repo.profile(key);
    const old = this.windows.get(key);
    if (old && !old.isDestroyed()) { old.focus(); return; }

    // Third-party pages never receive a preload, privileged IPC or node APIs.
    const win = new BrowserWindow({
      width: 1120, height: 800, minWidth: 460, minHeight: 420,
      show: false, title: profile.label, autoHideMenuBar: true,
      backgroundColor: '#141b2a',
      webPreferences: {
        partition: profile.storagePartition, contextIsolation: true,
        nodeIntegration: false, sandbox: true, webviewTag: false,
        webSecurity: true,
      },
    });
    this.windows.set(key, win);
    win.once('ready-to-show', () => win.show());
    win.on('closed', () => this.windows.delete(key));
    win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    win.webContents.on('will-navigate', (event, url) => {
      if (!/^https?:\/\//i.test(url) && url !== 'about:blank') event.preventDefault();
    });
    this.repo.recordProfileAction(key, 'open');
    try {
      await win.loadURL(profile.homeUrl);
    } catch {
      if (!win.isDestroyed()) win.show(); // No retries, HTTPS downgrade or hidden errors.
    }
  }

  close(value: unknown) {
    const key = id(value);
    this.repo.profile(key);
    const win = this.windows.get(key);
    if (win && !win.isDestroyed()) win.close();
    this.repo.recordProfileAction(key, 'close');
  }

  async clear(value: unknown) {
    const key = id(value), profile = this.repo.profile(key);
    const win = this.windows.get(key);
    if (win && !win.isDestroyed()) win.close();
    const browserSession = session.fromPartition(profile.storagePartition);
    await browserSession.clearStorageData();
    await browserSession.clearCache();
    this.repo.recordProfileAction(key, 'clear');
  }

  async remove(value: unknown) {
    const key = id(value);
    await this.clear(key);
    this.repo.removeProfile(key);
  }

  closeAll() {
    for (const win of this.windows.values()) if (!win.isDestroyed()) win.close();
    this.windows.clear();
  }
}
