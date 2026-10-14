export type AccountStatus = 'draft' | 'ready' | 'paused';
export interface BrowserProfile {
  id: string; label: string; homeUrl: string; storagePartition: string;
  createdAt: string; updatedAt: string;
}
export interface Account {
  id: string; provider: string; displayName: string;
  parentAccountId: string | null; profileId: string | null;
  status: AccountStatus; createdAt: string; updatedAt: string;
}
export interface AuditEvent {
  id: number; actor: string; action: string; entityType: string;
  entityId: string; result: string; timestamp: string;
}
export interface AccountInput {
  provider: string; displayName: string; parentAccountId?: string | null;
  profileId?: string | null; status?: AccountStatus;
}
export interface ProfileInput { label: string; homeUrl: string; }
export interface AccountUpdate extends Partial<AccountInput> { id: string; }
export interface ProfileUpdate extends Partial<ProfileInput> { id: string; }
export interface DesktopApi {
  listAccounts(): Promise<Account[]>;
  createAccount(input: AccountInput): Promise<Account>;
  updateAccount(input: AccountUpdate): Promise<Account>;
  deleteAccount(id: string): Promise<void>;
  importAccounts(input: AccountInput[]): Promise<number>;
  listProfiles(): Promise<BrowserProfile[]>;
  createProfile(input: ProfileInput): Promise<BrowserProfile>;
  updateProfile(input: ProfileUpdate): Promise<BrowserProfile>;
  deleteProfile(id: string): Promise<void>;
  openProfile(id: string): Promise<void>;
  closeProfile(id: string): Promise<void>;
  clearProfile(id: string): Promise<void>;
  listAudit(): Promise<AuditEvent[]>;
}
