import { contextBridge, ipcRenderer } from 'electron';
import type { DesktopApi } from '../shared/contracts';

const api = Object.freeze<DesktopApi>({
  listAccounts: () => ipcRenderer.invoke('accounts:list'),
  createAccount: (data) => ipcRenderer.invoke('accounts:create', data),
  updateAccount: (data) => ipcRenderer.invoke('accounts:update', data),
  deleteAccount: (key) => ipcRenderer.invoke('accounts:delete', key),
  importAccounts: (rows) => ipcRenderer.invoke('accounts:import', rows),
  listProfiles: () => ipcRenderer.invoke('profiles:list'),
  createProfile: (data) => ipcRenderer.invoke('profiles:create', data),
  updateProfile: (data) => ipcRenderer.invoke('profiles:update', data),
  deleteProfile: (key) => ipcRenderer.invoke('profiles:delete', key),
  openProfile: (key) => ipcRenderer.invoke('profiles:open', key),
  closeProfile: (key) => ipcRenderer.invoke('profiles:close', key),
  clearProfile: (key) => ipcRenderer.invoke('profiles:clear', key),
  listAudit: () => ipcRenderer.invoke('audit:list'),
});
contextBridge.exposeInMainWorld('orbita', api);
