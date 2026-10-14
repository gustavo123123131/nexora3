import { ipcMain } from 'electron';
import type { Repository } from './database';
import type { ProfileWindows } from './profiles';

export function registerIpc(repo: Repository, profiles: ProfileWindows, mainWindowId: () => number | undefined) {
  function bind(channel: string, action: (...args: unknown[]) => unknown) {
    ipcMain.handle(channel, (event, ...args: unknown[]) => {
      // Do not accept account data/actions from profile browser windows.
      if (event.sender.id !== mainWindowId()) throw new Error('Origem IPC não autorizada.');
      return action(...args);
    });
  }
  // No dynamic invoke API exposed to renderer. Every command is allowlisted.
  bind('accounts:list', () => repo.listAccounts());
  bind('accounts:create', (input) => repo.createAccount(input));
  bind('accounts:update', (input) => repo.updateAccount(input));
  bind('accounts:delete', (key) => repo.removeAccount(key as string));
  bind('accounts:import', (rows) => repo.importAccounts(rows));
  bind('profiles:list', () => repo.listProfiles());
  bind('profiles:create', (input) => repo.createProfile(input));
  bind('profiles:update', (input) => repo.updateProfile(input));
  bind('profiles:delete', (key) => profiles.remove(key));
  bind('profiles:open', (key) => profiles.open(key));
  bind('profiles:close', (key) => profiles.close(key));
  bind('profiles:clear', (key) => profiles.clear(key));
  bind('audit:list', () => repo.listAudit());
}
